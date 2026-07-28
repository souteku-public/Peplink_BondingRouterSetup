"""デモモード: InControl2 に接続せず、tunnel_stat 相当の応答を合成する。

機器側の統計は約10秒ごとに更新される想定で、timestamp を10秒刻みでしか
進めない。→ ポーリング間隔を短くしても「更新された時だけ記録」される動きを
実機なしで確認できる。
"""

from __future__ import annotations

import math
import time


class DemoIC2Client:
    """IC2Client と同じインターフェースの合成データ版。"""

    UPDATE_PERIOD = 10  # 機器統計の更新周期(秒)

    def __init__(self, *_args, **_kwargs):
        self._t0 = time.time()
        self._counters: dict[tuple, dict] = {}

    def organizations(self):
        return [{"id": "demo-org", "name": "デモ組織", "status": "active"}]

    def groups(self, org_id):
        return [{"id": 1, "name": "デモグループ"}]

    def devices(self, org_id, group_id):
        return [
            {"id": 101, "sn": "1927-DEMO-0001", "name": "中継車1号(デモ)", "product_name": "MAX BR2 Pro", "status": "online"},
            {"id": 102, "sn": "2833-DEMO-0002", "name": "中継車2号(デモ)", "product_name": "MAX Transit Pro Duo", "status": "online"},
        ]

    def pepvpn_status(self, org_id, group_id, device_id):
        return [{"sn": self._sn(device_id), "peers": [{"id": "1-1", "state": "CONNECTED"}]}]

    # ------------------------------------------------------------ tunnel_stat

    def _sn(self, device_id):
        return {101: "1927-DEMO-0001", 102: "2833-DEMO-0002"}.get(int(device_id), f"DEMO-{device_id}")

    def _advance(self, key, elapsed, base_rate, phase):
        """累積カウンタを「それらしく」進める(sin波で帯域が揺れる)。"""
        state = self._counters.setdefault(key, {"rx": 10_000_000, "tx": 5_000_000, "loss": 0, "t": 0})
        dt = elapsed - state["t"]
        if dt > 0:
            wave = 1 + 0.4 * math.sin(elapsed / 23 + phase)
            state["rx"] += int(base_rate * wave * dt / 8)          # bytes
            state["tx"] += int(base_rate * 0.35 * wave * dt / 8)
            if int(elapsed) % 37 < 2:  # たまにロス
                state["loss"] += 3
            state["t"] = elapsed
        return state

    def tunnel_stat(self, org_id, group_id, device_id):
        now = time.time()
        # 機器統計は UPDATE_PERIOD 秒刻みでしか進まない
        quantized = int(now // self.UPDATE_PERIOD) * self.UPDATE_PERIOD
        elapsed = quantized - int(self._t0 // self.UPDATE_PERIOD) * self.UPDATE_PERIOD

        wans = {
            "1": ("Cellular 1", 40_000_000, 0.0),
            "2": ("Cellular 2", 25_000_000, 1.7),
        }
        wan_map = {}
        for conn_id, (name, rate, phase) in wans.items():
            c = self._advance((device_id, conn_id), elapsed, rate, phase + int(device_id))
            rtt = 38 + int(12 * math.sin(elapsed / 17 + phase + int(device_id))) + (14 if conn_id == "2" else 0)
            wan_map[conn_id] = {
                "name": name,
                "state": "ACTIVE",
                "rx": [c["rx"]],
                "tx": [c["tx"]],
                "rtt": rtt,
                "loss": [c["loss"]],
                "time": {"second": quantized, "nanoSecond": 0},
            }
        return [
            {
                "sn": self._sn(device_id),
                "timestamp": quantized,
                "organization_id": str(org_id),
                "status": 1,
                "stat": "ok",
                "tunnel_stat_list": {"1-1": wan_map},
            }
        ]
