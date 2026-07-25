#!/usr/bin/env python3
"""Peplink 設定管理コンソール 起動スクリプト。

使い方:
    python3 run.py --demo                 # 実機なしでデモデータを表示
    python3 run.py                        # config.yaml の機器に接続
    python3 run.py -c 別の設定.yaml        # 設定ファイルを指定
    python3 run.py --check                # 接続確認だけ行って終了(画面は起動しない)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parent))

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "PyYAML が見つかりません。次のコマンドでインストールしてください:\n"
        "    python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)

# パッケージとしても単体スクリプトとしても動くようにする
if __package__ in (None, ""):
    sys.path.insert(0, str(BASE_DIR))
    from server import app as app_mod  # type: ignore
    from server.demo import demo_config  # type: ignore
    from server.peplink import PeplinkClient, PeplinkError  # type: ignore
else:  # pragma: no cover
    from .server import app as app_mod
    from .server.demo import demo_config
    from .server.peplink import PeplinkClient, PeplinkError

PROFILE_DIR = BASE_DIR / "profiles"
DEFAULT_CONFIG = BASE_DIR / "config.yaml"


def load_profiles() -> dict[str, dict]:
    """profiles/*.yaml をすべて読み込む。"""
    profiles: dict[str, dict] = {}
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            print(f"警告: {path.name} を読み込めません: {exc}", file=sys.stderr)
            continue
        name = data.get("profile") or path.stem
        profiles[name] = data
    return profiles


def load_config(path: Path) -> dict:
    if not path.exists():
        print(
            f"設定ファイルが見つかりません: {path}\n"
            "config.example.yaml をコピーして config.yaml を作成してください:\n"
            "    cp config.example.yaml config.yaml\n"
            "実機なしで画面を確認する場合は --demo を付けて起動してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    devices = data.get("devices") or []
    if not devices:
        print(f"{path} に devices が定義されていません。", file=sys.stderr)
        raise SystemExit(1)
    for i, dev in enumerate(devices, 1):
        if not dev.get("id"):
            dev["id"] = f"device-{i}"
        if not dev.get("host"):
            print(f"{path}: {dev['id']} に host がありません。", file=sys.stderr)
            raise SystemExit(1)
    data.setdefault("allow_write", False)
    data["demo"] = False
    return data


def check_devices(config: dict, profiles: dict) -> int:
    """各機器へのログインと主要エンドポイントの疎通を確認する。"""
    failed = 0
    for dev in config["devices"]:
        label = dev.get("label") or dev["id"]
        print(f"--- {label} ({dev['host']})")
        client = PeplinkClient(
            dev["host"],
            port=dev.get("port"),
            scheme=dev.get("scheme", "https"),
            username=dev.get("username"),
            password=dev.get("password"),
            client_id=dev.get("client_id"),
            client_secret=dev.get("client_secret"),
            verify_tls=bool(dev.get("verify_tls", False)),
            timeout=float(dev.get("timeout", 15)),
            use_proxy=bool(dev.get("use_proxy", False)),
        )
        try:
            perm = client.login()
            print(f"    ログイン成功  権限: GET={perm.get('GET')} POST={perm.get('POST')}")
            for name, fn in (
                ("info.firmware", client.firmware),
                ("status.wan.connection", client.wan_status),
                ("config.wan.connection", client.wan_config),
                ("status.pepvpn", client.pepvpn_status),
            ):
                try:
                    fn()
                    print(f"    OK   {name}")
                except PeplinkError as exc:
                    print(f"    NG   {name}: {exc}")
            client.logout()
        except PeplinkError as exc:
            print(f"    失敗: {exc}")
            failed += 1
        profile = dev.get("profile")
        if profile and profile not in profiles:
            print(f"    警告: プロファイル '{profile}' が profiles/ にありません")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Peplink 設定管理コンソール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="設定ファイル(既定: config.yaml)")
    parser.add_argument("--demo", action="store_true", help="実機に接続せずデモデータで起動する")
    parser.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス(既定: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="待ち受けポート(既定: 8787)")
    parser.add_argument("--allow-write", action="store_true", help="設定変更を許可する(既定は参照専用)")
    parser.add_argument("--check", action="store_true", help="接続確認のみ行って終了する")
    args = parser.parse_args(argv)

    profiles = load_profiles()
    if not profiles:
        print("警告: profiles/ に監査プロファイルがありません。監査画面は空になります。", file=sys.stderr)

    if args.demo:
        config = demo_config()
    else:
        config = load_config(Path(args.config))

    if args.allow_write:
        config["allow_write"] = True

    if args.check:
        if config.get("demo"):
            print("デモモードでは接続確認は不要です。")
            return 0
        return check_devices(config, profiles)

    app_mod.serve(config, profiles, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
