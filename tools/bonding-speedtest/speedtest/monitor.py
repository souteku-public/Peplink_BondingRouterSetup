"""連続計測モード(--monitor)。

指定した時間(例: 2h / 90m / 45s)にわたって転送とRTTプローブを流し続け、
**1秒ごとに1行**のCSVを書き続ける。数時間の長時間計測を想定し、
  * 行は書くたびにflush(途中で強制終了してもそこまでのデータは残る)
  * 1行は約80バイト → 1時間で約0.3MB、10時間でも約3MBに収まる
  * メモリには直近のRTTサンプル位置しか持たない(実行時間に比例して増えない)

CSV列:
  time        記録時刻(ローカルタイムISO形式)
  elapsed_s   計測開始からの経過秒
  down_mbps   その1秒間の下りスループット(Mbps)
  up_mbps     その1秒間の上りスループット(Mbps)
  rtt_ms      その1秒間のRTT平均(ミリ秒)
  rtt_max_ms  その1秒間のRTT最大
  rtt_fail    その1秒間に失敗したRTTプローブ数(0以外は疎通異常の兆候)
"""

from __future__ import annotations

import csv
import datetime
import re
import threading
import time
from pathlib import Path

from . import engine


def parse_duration(text: str) -> float:
    """"2h" "90m" "45s" "1h30m" "3600" を秒に変換する。"""
    text = str(text).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    if total <= 0:
        raise ValueError(f"時間指定を解釈できません: {text!r}(例: 2h / 90m / 45s / 1h30m)")
    return total


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m:02d}分{s:02d}秒"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


def run_monitor(targets: dict, duration_s: float, output_dir: Path, *,
                limit_bytes: int | None = None,
                do_download: bool = True, do_upload: bool = True,
                stamp: str | None = None) -> dict:
    """連続計測を実行し、サマリ(save_results互換のphase辞書)を返す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.datetime.now().astimezone()
    stamp = stamp or start_dt.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"monitor_{stamp}.csv"

    verify = bool(targets.get("verify_tls", True))
    use_proxy = bool(targets.get("use_proxy", False))
    streams = int(targets.get("streams", 8))
    up_streams = int(targets.get("upload_streams") or streams)
    rtt_host, rtt_port = targets["rtt_host"], int(targets.get("rtt_port", 443))

    # ---- 無負荷RTT(開始前に3秒だけ)
    idle = engine.measure_idle_rtt(rtt_host, rtt_port, seconds=3.0)

    stop = threading.Event()
    errors: list[str] = []
    deadline = time.time() + duration_s
    down_counter = engine.ByteCounter(limit_bytes)
    up_counter = engine.ByteCounter(limit_bytes)

    threads: list[threading.Thread] = []
    if do_download:
        url = targets["download_url"].replace(
            "{bytes}", str(targets.get("download_object_bytes", 67108864)))

        def dworker():
            engine._download_worker(url, down_counter, deadline, stop, errors, verify, use_proxy)

        threads += [threading.Thread(target=dworker, daemon=True) for _ in range(streams)]
    if do_upload:
        def uworker():
            engine._upload_worker(targets["upload_url"], up_counter, deadline, stop, errors, verify)

        threads += [threading.Thread(target=uworker, daemon=True) for _ in range(up_streams)]

    rtt = engine.RttSampler(rtt_host, rtt_port)
    rtt.start()
    for t in threads:
        t.start()

    peak_down = peak_up = 0.0
    rtt_all_max = 0.0
    zero_streak = 0
    rows = 0
    rtt_cursor = 0
    fail_cursor = 0
    prev_down = prev_up = 0
    start = time.time()
    next_tick = start + 1.0
    last_status = start

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "elapsed_s", "down_mbps", "up_mbps",
                         "rtt_ms", "rtt_max_ms", "rtt_fail"])
        f.flush()
        try:
            while time.time() < deadline and not stop.is_set():
                time.sleep(max(0.0, next_tick - time.time()))
                now = time.time()
                next_tick += 1.0

                d_total, u_total = down_counter.total, up_counter.total
                down_mbps = (d_total - prev_down) * 8 / 1e6
                up_mbps = (u_total - prev_up) * 8 / 1e6
                prev_down, prev_up = d_total, u_total

                second_rtt = rtt.samples[rtt_cursor:]
                rtt_cursor = len(rtt.samples)
                fails = rtt.failures - fail_cursor
                fail_cursor = rtt.failures
                rtt_avg = round(sum(second_rtt) / len(second_rtt), 1) if second_rtt else ""
                rtt_max = round(max(second_rtt), 1) if second_rtt else ""

                writer.writerow([
                    datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    round(now - start, 1),
                    round(down_mbps, 2) if do_download else "",
                    round(up_mbps, 2) if do_upload else "",
                    rtt_avg, rtt_max, fails,
                ])
                f.flush()
                rows += 1
                peak_down = max(peak_down, down_mbps)
                peak_up = max(peak_up, up_mbps)
                if second_rtt:
                    rtt_all_max = max(rtt_all_max, max(second_rtt))

                # 転送が完全に止まっている場合の警告(RTTのみの計測では出さない)
                if (do_download or do_upload) and down_mbps == 0 and up_mbps == 0:
                    zero_streak += 1
                    if zero_streak == 30:
                        print(f"\n⚠ 30秒間データが流れていません。測定先・回線の状態を確認してください。"
                              + (f" 直近のエラー: {errors[-1]}" if errors else ""))
                else:
                    zero_streak = 0

                if now - last_status >= 10:
                    elapsed = format_duration(now - start)
                    remain = format_duration(max(0, deadline - now))
                    line = f"[{time.strftime('%H:%M:%S')}] 経過 {elapsed} / 残り {remain}"
                    if do_download:
                        line += f"  下り {down_mbps:7.1f} Mbps"
                    if do_upload:
                        line += f"  上り {up_mbps:7.1f} Mbps"
                    if rtt_avg != "":
                        line += f"  RTT {rtt_avg} ms"
                    line += f"  使用 {(d_total + u_total) / 1e9:.2f} GB"
                    print(line)
                    last_status = now

                if limit_bytes and (d_total >= limit_bytes or u_total >= limit_bytes):
                    print("\nデータ量上限に達したため計測を終了します。")
                    break
        except KeyboardInterrupt:
            print("\n中断されました。ここまでのデータは保存されています。")
        finally:
            stop.set()
            rtt.stop()
            for t in threads:
                t.join(timeout=3)
            rtt.join(timeout=2)

    elapsed_total = max(1.0, time.time() - start)
    d_total, u_total = down_counter.total, up_counter.total
    summary = {
        "measured_at": start_dt.isoformat(timespec="seconds"),
        "phase": f"連続計測({format_duration(elapsed_total)})",
        "wan_id": None,
        "rtt_idle": idle,
        "notes": f"monitor csv={csv_path.name}",
        "download": {
            "direction": "download", "seconds": round(elapsed_total, 1), "bytes": d_total,
            "avg_mbps": round(d_total * 8 / elapsed_total / 1e6, 1),
            "peak_mbps": round(peak_down, 1),
            "loaded_rtt": engine.rtt_stats(rtt.samples[-2000:]),
            "errors": errors[:5],
        } if do_download else None,
        "upload": {
            "direction": "upload", "seconds": round(elapsed_total, 1), "bytes": u_total,
            "avg_mbps": round(u_total * 8 / elapsed_total / 1e6, 1),
            "peak_mbps": round(peak_up, 1),
            "loaded_rtt": {},
            "errors": [],
        } if do_upload else None,
        "monitor_csv": str(csv_path),
        "rows": rows,
    }
    print(f"\n計測終了: {rows}行を記録しました → {csv_path}")
    print(f"  下り 平均 {summary['download']['avg_mbps'] if do_download else '—'} / "
          f"ピーク {round(peak_down,1) if do_download else '—'} Mbps"
          f"   上り 平均 {summary['upload']['avg_mbps'] if do_upload else '—'} / "
          f"ピーク {round(peak_up,1) if do_upload else '—'} Mbps"
          f"   RTT最大 {round(rtt_all_max,1)} ms   合計 {(d_total+u_total)/1e9:.2f} GB")
    return summary
