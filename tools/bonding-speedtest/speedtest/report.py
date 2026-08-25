"""測定結果の表示とファイル出力。"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

CSV_FIELDS = [
    "measured_at",       # 測定開始時刻
    "phase",             # bonded / 各WAN名
    "wan_id",            # 単独測定時のWAN ID(ボンディングは空)
    "download_avg_mbps",
    "download_peak_mbps",
    "upload_avg_mbps",
    "upload_peak_mbps",
    "rtt_idle_ms",       # 無負荷RTT(平均)
    "rtt_idle_p95_ms",
    "rtt_down_ms",       # ダウンロード中RTT(平均)= バッファブロート指標
    "rtt_up_ms",         # アップロード中RTT(平均)
    "download_bytes",
    "upload_bytes",
    "notes",
]


def _mb(n: int | None) -> str:
    return f"{n / 1e6:,.0f}MB" if n else "0MB"


def print_phase(phase: dict) -> None:
    """1フェーズ分の結果をコンソールに表示する。"""
    name = phase["phase"]
    down, up, idle = phase.get("download"), phase.get("upload"), phase.get("rtt_idle") or {}
    print(f"\n┌── {name}")
    if idle.get("avg") is not None:
        print(f"│ RTT(無負荷)     : {idle['avg']} ms (min {idle['min']} / p95 {idle['p95']})")
    if down:
        rtt = (down.get("loaded_rtt") or {}).get("avg")
        print(f"│ 下り  平均 {down['avg_mbps'] or '—':>7} Mbps / ピーク {down['peak_mbps'] or '—':>7} Mbps"
              f"   RTT(負荷中) {rtt or '—'} ms   使用 {_mb(down['bytes'])}")
    if up:
        rtt = (up.get("loaded_rtt") or {}).get("avg")
        print(f"│ 上り  平均 {up['avg_mbps'] or '—':>7} Mbps / ピーク {up['peak_mbps'] or '—':>7} Mbps"
              f"   RTT(負荷中) {rtt or '—'} ms   使用 {_mb(up['bytes'])}")
    errors = (down or {}).get("errors", []) + (up or {}).get("errors", [])
    if errors:
        print(f"│ ⚠ エラー: {errors[0]}" + (f" ほか{len(errors)-1}件" % () if len(errors) > 1 else ""))
    print("└" + "─" * 60)


def _width(text: str) -> int:
    """全角文字を2桁として数える表示幅。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "FWA" else 1 for ch in text)


def _pad(text: str, width: int, *, right: bool = False) -> str:
    gap = max(0, width - _width(text))
    return (" " * gap + text) if right else (text + " " * gap)


def print_summary_table(phases: list[dict]) -> None:
    """全フェーズの比較表(回線ごとのポテンシャル一覧)。"""
    if len(phases) < 2:
        return
    print("\n=== 回線ポテンシャル比較 ===")
    cols = [("下り平均", 10), ("下りピーク", 12), ("上り平均", 10), ("上りピーク", 12),
            ("RTT", 7), ("負荷中RTT", 11)]
    header = _pad("回線", 26) + "".join(_pad(name, w, right=True) for name, w in cols)
    print(header)
    print("-" * _width(header))
    for p in phases:
        down, up = p.get("download") or {}, p.get("upload") or {}
        idle = (p.get("rtt_idle") or {}).get("avg")
        loaded = (down.get("loaded_rtt") or {}).get("avg")
        values = [down.get("avg_mbps"), down.get("peak_mbps"),
                  up.get("avg_mbps"), up.get("peak_mbps"), idle, loaded]
        row = _pad(p["phase"], 26)
        for (_name, width), value in zip(cols, values):
            row += _pad(str(value) if value is not None else "—", width, right=True)
        print(row)
    print("(単位: Mbps / ms)")


def save_results(phases: list[dict], output_dir: Path, *, samples: bool = False) -> list[Path]:
    """summary CSV(追記)+ 実行ごとのJSONを保存し、書いたパスを返す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone()
    written: list[Path] = []

    # --- サマリCSV(1フェーズ=1行、実行のたびに追記して履歴になる)
    summary = output_dir / "speedtest_summary.csv"
    new_file = not summary.exists()
    with summary.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        for p in phases:
            down, up = p.get("download") or {}, p.get("upload") or {}
            writer.writerow({
                "measured_at": p.get("measured_at") or now.isoformat(timespec="seconds"),
                "phase": p["phase"],
                "wan_id": p.get("wan_id") or "",
                "download_avg_mbps": down.get("avg_mbps"),
                "download_peak_mbps": down.get("peak_mbps"),
                "upload_avg_mbps": up.get("avg_mbps"),
                "upload_peak_mbps": up.get("peak_mbps"),
                "rtt_idle_ms": (p.get("rtt_idle") or {}).get("avg"),
                "rtt_idle_p95_ms": (p.get("rtt_idle") or {}).get("p95"),
                "rtt_down_ms": (down.get("loaded_rtt") or {}).get("avg"),
                "rtt_up_ms": (up.get("loaded_rtt") or {}).get("avg"),
                "download_bytes": down.get("bytes"),
                "upload_bytes": up.get("bytes"),
                "notes": p.get("notes") or "",
            })
    written.append(summary)

    # --- 実行ごとの詳細JSON
    stamp = now.strftime("%Y%m%d_%H%M%S")
    detail = output_dir / f"speedtest_{stamp}.json"
    payload = []
    for p in phases:
        entry = dict(p)
        if not samples:
            for key in ("download", "upload"):
                if isinstance(entry.get(key), dict):
                    entry[key] = {k: v for k, v in entry[key].items() if k != "samples"}
        payload.append(entry)
    detail.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(detail)

    # --- 秒別サンプルCSV(オプション)
    if samples:
        path = output_dir / f"speedtest_{stamp}_samples.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["phase", "direction", "elapsed_s", "interval_bytes", "interval_mbps"])
            for p in phases:
                for key in ("download", "upload"):
                    data = p.get(key) or {}
                    for t, b in data.get("samples") or []:
                        writer.writerow([p["phase"], key, t, b, round(b * 8 / 0.25 / 1e6, 1)])
        written.append(path)
    return written
