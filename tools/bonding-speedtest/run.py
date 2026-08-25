#!/usr/bin/env python3
"""ボンディング回線スループット測定 起動スクリプト。

使い方:
    python3 run.py                    # ボンディング(現在の構成のまま)の最大スループットとRTTを測定
    python3 run.py --per-wan          # さらに回線を1本ずつ切り替えて、各回線のポテンシャルを測定
    python3 run.py --demo             # ネットワークを使わずローカルで動作確認
    python3 run.py --serve            # 測定先(リフレクター)サーバーとして起動
"""

from __future__ import annotations

import argparse
import datetime
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

from speedtest import engine, monitor, report, testserver
from speedtest.router import PrioritySnapshot, RouterClient, RouterError

DEFAULT_CONFIG = BASE_DIR / "config.yaml"

DEFAULT_TARGETS = {
    "download_url": "https://speed.cloudflare.com/__down?bytes={bytes}",
    "upload_url": "https://speed.cloudflare.com/__up",
    "rtt_host": "speed.cloudflare.com",
    "rtt_port": 443,
}


def load_config(path: Path, *, demo: bool) -> dict:
    if demo:
        return {"demo": True, "duration": 4, "streams": 4, "output_dir": str(BASE_DIR / "results")}
    if not path.exists():
        # 測定先が既定(Cloudflare)で良ければ設定ファイル無しでも動かせる
        print(f"注: {path.name} が無いため既定設定(測定先: Cloudflare、12秒×8並列)で実行します。", file=sys.stderr)
        return {"output_dir": str(BASE_DIR / "results")}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("output_dir", str(BASE_DIR / "results"))
    return data


def build_targets(config: dict) -> dict:
    cfg = dict(DEFAULT_TARGETS)
    cfg.update({k: v for k, v in (config.get("targets") or {}).items() if v is not None})
    for key in ("duration", "streams", "upload_streams", "verify_tls", "use_proxy",
                "download_object_bytes"):
        if config.get(key) is not None:
            cfg[key] = config[key]
    return cfg


# ---------------------------------------------------------------------------
# 測定フェーズ
# ---------------------------------------------------------------------------


def run_phase(name: str, targets: dict, limit_bytes: int | None, *,
              wan_id: int | None = None, skip_upload: bool = False,
              skip_download: bool = False) -> dict:
    """無負荷RTT → 下り → 上り の順に1フェーズ測定する。"""
    print(f"\n▶ {name} を測定中…")
    measured_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    print("  ・RTT(無負荷)…", end="", flush=True)
    idle = engine.measure_idle_rtt(targets["rtt_host"], int(targets.get("rtt_port", 443)))
    print(f" {idle['avg']} ms" if idle["avg"] is not None else " 測定不可")

    down = up = None
    if not skip_download:
        print(f"  ・下り({targets.get('duration', 12)}秒 × {targets.get('streams', 8)}並列)…", end="", flush=True)
        down = engine.measure_download(targets, limit_bytes)
        print(f" 平均 {down.avg_mbps} / ピーク {down.peak_mbps} Mbps")
    if not skip_upload:
        print(f"  ・上り({targets.get('duration', 12)}秒)…", end="", flush=True)
        up = engine.measure_upload(targets, limit_bytes)
        print(f" 平均 {up.avg_mbps} / ピーク {up.peak_mbps} Mbps")

    phase = {
        "measured_at": measured_at,
        "phase": name,
        "wan_id": wan_id,
        "rtt_idle": idle,
        "download": {**down.to_json(), "samples": down.samples} if down else None,
        "upload": {**up.to_json(), "samples": up.samples} if up else None,
    }
    report.print_phase(phase)
    return phase


def run_per_wan(config: dict, targets: dict, limit_bytes: int | None,
                *, assume_yes: bool, skip_upload: bool = False) -> list[dict]:
    router_conf = config.get("router")
    if not router_conf or not router_conf.get("host"):
        print(
            "--per-wan には config.yaml の router セクション(機器のIPと認証情報)が必要です。\n"
            "config.example.yaml を参照してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("\n" + "=" * 64)
    print("回線ごとの測定は、機器のWAN優先度を一時的に切り替えます。")
    print("  ・測定中、通信は対象回線1本に集中します(他の通信にも影響します)")
    print("  ・各回線の切替時に数秒〜数十秒の経路変更が発生します")
    print("  ・測定終了後、優先度は自動的に元へ戻します")
    print("  ・本番運用中には実行しないでください")
    print("=" * 64)
    if not assume_yes:
        answer = input("実行しますか? [yes/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("回線ごとの測定をスキップしました。")
            return []

    client = RouterClient(router_conf)
    client.login()
    snapshot = PrioritySnapshot(client)
    wans = snapshot.capture()
    candidates = [w for w in wans if w.get("enable") and w.get("priority")]
    print(f"対象回線: {', '.join(w['name'] for w in candidates)}")

    phases: list[dict] = []
    settle = float(config.get("settle_seconds", 5))
    try:
        for wan in candidates:
            print(f"\n── {wan['name']} だけを優先度1に切り替えます…")
            snapshot.isolate(wan["id"])
            if not client.wait_connected(wan["id"], timeout=60):
                print(f"  ⚠ {wan['name']} が時間内に接続状態になりませんでした。この回線はスキップします。")
                continue
            time.sleep(settle)  # 経路安定待ち
            phase = run_phase(f"単独: {wan['name']}", targets, limit_bytes, wan_id=wan["id"],
                              skip_upload=skip_upload)
            phase["notes"] = "per-wan"
            phases.append(phase)
    finally:
        print("\n優先度を元の設定へ復元します…", end="", flush=True)
        try:
            snapshot.restore()
            print(" 完了")
        except RouterError as exc:
            print(f"\n⚠⚠ 復元に失敗しました: {exc}")
            print("   Web管理画面のダッシュボードで優先度を手動で確認・修正してください。")
            print(f"   元の設定: {snapshot.original}")
    return phases


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ボンディング回線スループット測定")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="設定ファイル(既定: config.yaml)")
    parser.add_argument("--monitor", metavar="時間",
                        help="連続計測モード: 指定時間、1秒ごとにCSVへ記録する(例: 2h / 90m / 45s / 1h30m)")
    parser.add_argument("--per-wan", action="store_true", help="回線を1本ずつ切り替えて各回線のポテンシャルも測定する")
    parser.add_argument("--yes", action="store_true", help="--per-wan の確認プロンプトを省略する")
    parser.add_argument("--duration", type=float, help="1方向あたりの測定秒数(既定12秒)")
    parser.add_argument("--streams", type=int, help="並列コネクション数(既定8)")
    parser.add_argument("--limit-mb", type=float, help="1方向あたりのデータ量上限(MB)。従量SIMの保護用")
    parser.add_argument("--no-upload", action="store_true", help="上り測定を行わない")
    parser.add_argument("--no-download", action="store_true",
                        help="下り測定を行わない(--monitorと併用でRTTだけの長時間計測=データ消費ほぼゼロ)")
    parser.add_argument("--samples", action="store_true", help="0.25秒刻みの明細CSVも保存する")
    parser.add_argument("--demo", action="store_true", help="ネットワークを使わずローカルで動作確認")
    parser.add_argument("--serve", action="store_true", help="測定先(リフレクター)サーバーとして起動する")
    parser.add_argument("--host", default="0.0.0.0", help="--serve の待ち受けアドレス")
    parser.add_argument("--port", type=int, default=8123, help="--serve の待ち受けポート")
    args = parser.parse_args(argv)

    if args.serve:
        testserver.serve(args.host, args.port)
        return 0

    config = load_config(Path(args.config), demo=args.demo)
    if args.duration:
        config["duration"] = args.duration
    if args.streams:
        config["streams"] = args.streams

    demo_server = None
    if args.demo:
        demo_server = testserver.start_background(18123)
        config["targets"] = {
            "download_url": "http://127.0.0.1:18123/__down?bytes={bytes}",
            "upload_url": "http://127.0.0.1:18123/__up",
            "rtt_host": "127.0.0.1",
            "rtt_port": 18123,
        }
        print("デモモード: ローカルのリフレクターに対して測定します(実回線は使いません)。")

    targets = build_targets(config)
    limit_bytes = int(args.limit_mb * 1e6) if args.limit_mb else (
        int(float(config["limit_mb"]) * 1e6) if config.get("limit_mb") else None
    )

    if args.monitor:
        try:
            duration_s = monitor.parse_duration(args.monitor)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"連続計測モード: {monitor.format_duration(duration_s)} / 1秒ごとにCSVへ記録します")
        print(f"測定先: {targets['download_url'].split('?')[0]}")
        if not limit_bytes and not args.demo and not (args.no_download and args.no_upload):
            print("注意: 飽和転送を続けるため、例えば100Mbpsの回線では1時間あたり約45GB(片方向)を消費します。")
            print("      従量SIMでは --limit-mb を指定するか、--no-download --no-upload(RTTのみ)を検討してください。")
        try:
            summary = monitor.run_monitor(
                targets, duration_s, Path(config["output_dir"]),
                limit_bytes=limit_bytes,
                do_download=not args.no_download, do_upload=not args.no_upload)
        finally:
            if demo_server is not None:
                demo_server.shutdown()
        report.save_results([summary], Path(config["output_dir"]))
        print(f"ビューワーで確認: viewer.html をブラウザで開き、{summary['monitor_csv']} を読み込んでください。")
        return 0

    est = f"{targets.get('duration', 12)}秒 × 2方向"
    print(f"測定先: {targets['download_url'].split('?')[0]}")
    print(f"測定時間: {est}/フェーズ" + (f" / データ量上限: {args.limit_mb or config.get('limit_mb')}MB×方向" if limit_bytes else ""))
    if not limit_bytes and not args.demo:
        print("注意: 高速回線では1フェーズで数百MB〜数GBを消費します。従量SIMでは --limit-mb を推奨します。")

    phases: list[dict] = []
    try:
        phases.append(run_phase("ボンディング(現構成)", targets, limit_bytes,
                                skip_upload=args.no_upload))
        if args.per_wan:
            phases += run_per_wan(config, targets, limit_bytes, assume_yes=args.yes,
                                  skip_upload=args.no_upload)
    except KeyboardInterrupt:
        print("\n中断されました。ここまでの結果を保存します。")
    finally:
        if demo_server is not None:
            demo_server.shutdown()

    if not phases:
        return 1
    report.print_summary_table(phases)
    written = report.save_results(phases, Path(config["output_dir"]), samples=args.samples)
    print("\n保存先:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
