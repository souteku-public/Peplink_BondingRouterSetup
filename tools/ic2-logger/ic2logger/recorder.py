"""tunnel_stat の変化検知と CSV / JSONL 書き出し。

「更新されたときだけ記録する」仕組み:
  tunnel_stat 応答には機器側で統計が採取された `timestamp`(および WAN ごとの
  time.second/nanoSecond)が含まれる。前回書き出した timestamp と同じ間は
  何度ポーリングしても記録せず、変わった時だけ1レコード書く。
  → ポーリング間隔を短くしても、CSVには実際のデータ更新の粒度で行が増える。

CSV列:
  logged_at      ロガーが記録した時刻(ローカルタイムISO形式)
  device_ts      機器側統計のUNIX時刻(tunnel_stat の timestamp)
  sn             機器シリアル番号
  peer_id        SpeedFusion ピアID
  conn_id        WAN接続ID
  wan_name       WAN名
  state          トンネル状態(ACTIVE など)
  rtt_ms         遅延(ミリ秒)
  loss           ドロップ数(応答の loss 配列の合計)
  rx_bytes       受信バイト累計(rx 配列の合計)
  tx_bytes       送信バイト累計(tx 配列の合計)
  rx_bps         受信スループット(前回レコードとの差分から計算、bit/秒)
  tx_bps         送信スループット(同上)
  loss_delta     前回レコードからのドロップ増分
"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path
from typing import Any


def _num_sum(value: Any) -> int | None:
    """rx/tx/loss は数値または数値配列で返るため、合計して1つの数値に潰す。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, list):
        total = 0
        found = False
        for v in value:
            if isinstance(v, (int, float)):
                total += int(v)
                found = True
        return total if found else None
    return None


def parse_tunnel_stat(data: Any) -> list[dict]:
    """tunnel_stat 応答(dataフィールド)を機器ごとのフラットな構造に変換する。

    返り値: [{sn, timestamp, wans: [{peer_id, conn_id, name, state, rtt, loss, rx, tx, t_second}]}]
    """
    out: list[dict] = []
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    for dev in data:
        if not isinstance(dev, dict):
            continue
        rows: list[dict] = []
        stat_list = dev.get("tunnel_stat_list") or {}
        for peer_id, wan_map in stat_list.items():
            if not isinstance(wan_map, dict):
                continue
            for conn_id, wan in wan_map.items():
                if conn_id == "order" or not isinstance(wan, dict):
                    continue
                t = wan.get("time") or {}
                rows.append(
                    {
                        "peer_id": str(peer_id),
                        "conn_id": str(conn_id),
                        "name": wan.get("name"),
                        "state": wan.get("state"),
                        "rtt": wan.get("rtt"),
                        "loss": _num_sum(wan.get("loss")),
                        "rx": _num_sum(wan.get("rx")),
                        "tx": _num_sum(wan.get("tx")),
                        "t_second": (t.get("second") if isinstance(t, dict) else None)
                        or wan.get("stime"),
                    }
                )
        out.append(
            {
                "sn": dev.get("sn") or "unknown",
                "timestamp": dev.get("timestamp"),
                "stat": dev.get("stat") or dev.get("status"),
                "wans": rows,
            }
        )
    return out


CSV_FIELDS = [
    "logged_at",
    "device_ts",
    "sn",
    "peer_id",
    "conn_id",
    "wan_name",
    "state",
    "rtt_ms",
    "loss",
    "rx_bytes",
    "tx_bytes",
    "rx_bps",
    "tx_bps",
    "loss_delta",
]


class Recorder:
    """機器1台分の変化検知・レート計算・ファイル書き出しを担当する。"""

    def __init__(self, output_dir: Path, *, formats: tuple[str, ...] = ("csv",), label: str | None = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.formats = formats
        self.label = label
        # (sn) -> 最後に記録した timestamp
        self._last_ts: dict[str, Any] = {}
        # (sn, peer, conn) -> 前回レコード {t, rx, tx, loss}(レート計算用)
        self._prev: dict[tuple, dict] = {}
        self.rows_written = 0
        self.samples_skipped = 0

    # ------------------------------------------------------------ 出力先

    def _file_for(self, sn: str, ext: str, when: datetime.datetime) -> Path:
        base = self.label or sn
        return self.output_dir / f"{base}_{when.strftime('%Y%m%d')}.{ext}"

    def _write_csv(self, rows: list[dict], when: datetime.datetime, sn: str) -> None:
        path = self._file_for(sn, "csv", when)
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)

    def _write_jsonl(self, record: dict, when: datetime.datetime, sn: str) -> None:
        path = self._file_for(sn, "jsonl", when)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ 記録

    def record(self, tunnel_stat_data: Any) -> int:
        """tunnel_stat 応答を処理し、書き出した行数を返す(更新が無ければ0)。"""
        written = 0
        now = datetime.datetime.now().astimezone()
        for dev in parse_tunnel_stat(tunnel_stat_data):
            sn = dev["sn"]
            ts = dev.get("timestamp")
            if ts is not None and self._last_ts.get(sn) == ts:
                # 機器側の統計が前回から更新されていない → 記録しない
                self.samples_skipped += 1
                continue
            self._last_ts[sn] = ts

            csv_rows: list[dict] = []
            for wan in dev["wans"]:
                key = (sn, wan["peer_id"], wan["conn_id"])
                prev = self._prev.get(key)
                # 経過時間: 機器側タイムスタンプを優先、無ければ記録時刻
                t_now = wan.get("t_second") or ts or now.timestamp()
                rx_bps = tx_bps = loss_delta = None
                if prev and prev.get("t") and t_now and t_now > prev["t"]:
                    dt = t_now - prev["t"]
                    if wan.get("rx") is not None and prev.get("rx") is not None and wan["rx"] >= prev["rx"]:
                        rx_bps = round((wan["rx"] - prev["rx"]) * 8 / dt)
                    if wan.get("tx") is not None and prev.get("tx") is not None and wan["tx"] >= prev["tx"]:
                        tx_bps = round((wan["tx"] - prev["tx"]) * 8 / dt)
                    if wan.get("loss") is not None and prev.get("loss") is not None and wan["loss"] >= prev["loss"]:
                        loss_delta = wan["loss"] - prev["loss"]
                self._prev[key] = {"t": t_now, "rx": wan.get("rx"), "tx": wan.get("tx"), "loss": wan.get("loss")}

                csv_rows.append(
                    {
                        "logged_at": now.isoformat(timespec="seconds"),
                        "device_ts": ts,
                        "sn": sn,
                        "peer_id": wan["peer_id"],
                        "conn_id": wan["conn_id"],
                        "wan_name": wan["name"],
                        "state": wan["state"],
                        "rtt_ms": wan["rtt"],
                        "loss": wan["loss"],
                        "rx_bytes": wan["rx"],
                        "tx_bytes": wan["tx"],
                        "rx_bps": rx_bps,
                        "tx_bps": tx_bps,
                        "loss_delta": loss_delta,
                    }
                )

            if not csv_rows:
                continue
            if "csv" in self.formats:
                self._write_csv(csv_rows, now, sn)
            if "jsonl" in self.formats:
                self._write_jsonl(
                    {"logged_at": now.isoformat(timespec="seconds"), "raw": dev}, now, sn
                )
            written += len(csv_rows)
            self.rows_written += len(csv_rows)
        return written
