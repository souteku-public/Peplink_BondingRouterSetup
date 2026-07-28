#!/usr/bin/env python3
"""InControl2 SpeedFusion ロガー 起動スクリプト。

InControl2 のSpeedFusionタブに相当するデータ(ピア別×WAN別の
スループット・遅延・ドロップ)をポーリングし、機器側の統計が
更新されたタイミングだけ CSV / JSONL に記録する。

使い方:
    python3 run.py --list                 # 組織/グループ/機器のIDを一覧表示(初期設定用)
    python3 run.py                        # config.yaml に従って記録を開始
    python3 run.py --once                 # 1回だけ取得して内容を表示(疎通確認)
    python3 run.py --demo                 # InControl2に接続せず動作を確認
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    import yaml
except ImportError:
    print(
        "PyYAML が見つかりません。次のコマンドでインストールしてください:\n"
        "    python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)

from ic2logger.demo import DemoIC2Client
from ic2logger.ic2 import DEFAULT_API_BASE, IC2Client, IC2Error
from ic2logger.recorder import Recorder

DEFAULT_CONFIG = BASE_DIR / "config.yaml"


def load_config(path: Path, *, demo: bool) -> dict:
    if demo:
        return {
            "demo": True,
            "organization_id": "demo-org",
            "group_id": 1,
            "devices": [{"id": 101, "label": "relay-01-demo"}, {"id": 102, "label": "relay-02-demo"}],
            "interval": 3,
            "output_dir": str(BASE_DIR / "logs"),
            "formats": ["csv"],
        }
    if not path.exists():
        print(
            f"設定ファイルが見つかりません: {path}\n"
            "config.example.yaml をコピーして config.yaml を作成してください:\n"
            "    cp config.example.yaml config.yaml\n"
            "IDが分からない場合は、client_id/client_secret だけ書いて --list を実行してください。\n"
            "接続なしで動作を見る場合は --demo を付けてください。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("client_id", "client_secret"):
        if not data.get(key):
            print(f"{path} に {key} がありません。README の手順で発行してください。", file=sys.stderr)
            raise SystemExit(1)
    data.setdefault("interval", 5)
    data.setdefault("output_dir", str(BASE_DIR / "logs"))
    data.setdefault("formats", ["csv"])
    data["demo"] = False
    return data


def make_client(config: dict):
    if config.get("demo"):
        return DemoIC2Client()
    return IC2Client(
        config["client_id"],
        config["client_secret"],
        api_base=config.get("api_base") or DEFAULT_API_BASE,
        timeout=float(config.get("timeout", 30)),
    )


# ---------------------------------------------------------------------------
# --list: ID探索
# ---------------------------------------------------------------------------


def cmd_list(config: dict) -> int:
    client = make_client(config)
    try:
        orgs = client.organizations() or []
    except IC2Error as exc:
        print(f"取得に失敗しました: {exc}", file=sys.stderr)
        return 1
    for org in orgs:
        org_id = org.get("id")
        print(f"組織: {org.get('name')}  (organization_id: {org_id})")
        try:
            groups = client.groups(org_id) or []
        except IC2Error as exc:
            print(f"  グループ取得失敗: {exc}")
            continue
        for group in groups:
            group_id = group.get("id")
            print(f"  グループ: {group.get('name')}  (group_id: {group_id})")
            try:
                devices = client.devices(org_id, group_id) or []
            except IC2Error as exc:
                print(f"    機器取得失敗: {exc}")
                continue
            for dev in devices:
                print(
                    f"    機器: {dev.get('name')}  (device_id: {dev.get('id')}, "
                    f"S/N: {dev.get('sn')}, 状態: {dev.get('status')}, 製品: {dev.get('product_name')})"
                )
    print("\nconfig.yaml に organization_id / group_id / devices(id) を転記してください。")
    return 0


# ---------------------------------------------------------------------------
# --once: 疎通確認
# ---------------------------------------------------------------------------


def _fetch_tunnel_stat(client, config: dict, device: dict):
    """PENDING(IC2が機器へ問い合わせ中)を吸収して tunnel_stat を取得する。"""
    for _ in range(5):
        try:
            return client.tunnel_stat(config["organization_id"], config["group_id"], device["id"])
        except IC2Error as exc:
            if exc.status == 202:  # PENDING
                time.sleep(exc.retry_after or 2)
                continue
            raise
    raise IC2Error("PENDING のまま応答が確定しませんでした(機器がオフラインの可能性)")


def cmd_once(config: dict) -> int:
    client = make_client(config)
    ok = True
    for device in config.get("devices") or []:
        label = device.get("label") or device["id"]
        print(f"--- {label} (device_id: {device['id']})")
        try:
            data = _fetch_tunnel_stat(client, config, device)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
        except IC2Error as exc:
            print(f"    取得失敗: {exc}")
            ok = False
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 記録ループ
# ---------------------------------------------------------------------------


def cmd_record(config: dict) -> int:
    devices = config.get("devices") or []
    if not devices:
        print("config.yaml の devices が空です。--list でIDを調べて記入してください。", file=sys.stderr)
        return 1

    client = make_client(config)
    interval = max(1.0, float(config.get("interval", 5)))
    formats = tuple(config.get("formats") or ["csv"])
    output_dir = Path(config["output_dir"])
    recorders = {
        str(dev["id"]): Recorder(output_dir, formats=formats, label=dev.get("label"))
        for dev in devices
    }

    stop = {"flag": False}

    def _stop(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    mode = "デモモード(InControl2 に接続しません)" if config.get("demo") else "InControl2 に接続"
    print(f"SpeedFusion ロガー開始 — {mode}")
    print(f"機器数: {len(devices)} / ポーリング間隔: {interval}秒 / 出力: {output_dir} ({', '.join(formats)})")
    print("機器側の統計が更新されたタイミングのみ記録します。停止は Ctrl+C。")

    error_streak: dict[str, int] = {}
    last_report = time.time()
    while not stop["flag"]:
        cycle_start = time.time()
        for dev in devices:
            if stop["flag"]:
                break
            dev_id = str(dev["id"])
            label = dev.get("label") or dev_id
            try:
                data = _fetch_tunnel_stat(client, config, dev)
            except IC2Error as exc:
                streak = error_streak.get(dev_id, 0) + 1
                error_streak[dev_id] = streak
                if streak in (1, 5) or streak % 60 == 0:  # 同じエラーの連発はログを間引く
                    print(f"[{time.strftime('%H:%M:%S')}] {label}: 取得失敗({streak}回目): {exc}")
                if exc.status == 429:
                    time.sleep(exc.retry_after or 2)
                continue
            if error_streak.get(dev_id):
                print(f"[{time.strftime('%H:%M:%S')}] {label}: 復旧しました")
                error_streak[dev_id] = 0
            written = recorders[dev_id].record(data)
            if written:
                print(f"[{time.strftime('%H:%M:%S')}] {label}: {written}行 記録")

        # 1時間ごとに統計を表示
        if time.time() - last_report >= 3600:
            total = sum(r.rows_written for r in recorders.values())
            skipped = sum(r.samples_skipped for r in recorders.values())
            print(f"[{time.strftime('%H:%M:%S')}] 累計 {total}行 記録 / 未更新スキップ {skipped}回")
            last_report = time.time()

        elapsed = time.time() - cycle_start
        wait = max(0.0, interval - elapsed)
        deadline = time.time() + wait
        while not stop["flag"] and time.time() < deadline:
            time.sleep(min(0.5, deadline - time.time()) if deadline > time.time() else 0)

    total = sum(r.rows_written for r in recorders.values())
    print(f"\n停止しました。累計 {total}行 を記録しました。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="InControl2 SpeedFusion ロガー")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="設定ファイル(既定: config.yaml)")
    parser.add_argument("--list", action="store_true", help="組織/グループ/機器のIDを一覧表示して終了")
    parser.add_argument("--once", action="store_true", help="tunnel_stat を1回取得して表示(疎通確認)")
    parser.add_argument("--demo", action="store_true", help="InControl2 に接続せず合成データで動作確認")
    parser.add_argument("--interval", type=float, help="ポーリング間隔(秒)。config.yaml より優先")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config), demo=args.demo)
    if args.interval:
        config["interval"] = args.interval

    if args.list:
        return cmd_list(config)
    if args.once:
        return cmd_once(config)
    return cmd_record(config)


if __name__ == "__main__":
    raise SystemExit(main())
