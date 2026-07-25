"""デモモード用の擬似機器。

実機が無くても画面と監査ロジックを確認できるように、Router API と同じ形の
レスポンスを返すダミークライアントを提供する。collector.collect() は本物の
クライアントと区別せずに扱えるため、正規化・監査の経路は実機と同一になる。

デモデータは「監査で✕が出る機器」を意図的に含めている(2号機)。
"""

from __future__ import annotations

from typing import Any


class DemoClient:
    """PeplinkClient と同じインターフェースを持つ擬似クライアント。"""

    def __init__(self, dataset: dict[str, Any], *, can_write: bool = True):
        self._data = dataset
        self._can_write = can_write
        self.pending: list[dict] = []
        self.applied: list[dict] = []

    # ------------------------------------------------------------- 認証まわり
    def login(self):
        return {"GET": 1, "POST": 1 if self._can_write else 0}

    def logout(self):
        return None

    @property
    def can_write(self) -> bool:
        return self._can_write

    # ------------------------------------------------------------- 参照
    def firmware(self):
        return self._data["firmware"]

    def wan_status(self):
        return self._data["wan_status"]

    def wan_config(self):
        return self._data["wan_config"]

    def wan_allowance(self):
        return self._data["wan_allowance"]

    def lan_status(self):
        return self._data["lan_status"]

    def pepvpn_status(self):
        return self._data["pepvpn"]

    def location(self):
        return self._data.get("location")

    def clients(self):
        return self._data.get("clients", {})

    # ------------------------------------------------------------- 更新
    def update_wan_config(self, entries: list[dict]) -> Any:
        """保留リストに積み、デモデータ側にも反映して監査結果が変わるようにする。"""
        self.pending += entries
        for entry in entries:
            wan_id = str(entry.get("id"))
            target = self._data["wan_config"].get(wan_id)
            if isinstance(target, dict):
                _deep_merge(target, {k: v for k, v in entry.items() if k != "id"})
                _normalize_as_firmware(target)
        return {"applied": False}

    def update_wan_priority(self, entries: list[dict], *, instant: bool = False) -> Any:
        self.pending += entries
        for entry in entries:
            wan_id = str(entry.get("connId"))
            cfg = self._data["wan_config"].get(wan_id)
            if isinstance(cfg, dict):
                cfg.setdefault("connection", {})["priority"] = entry.get("priority")
        return {"applied": False}

    def apply_changes(self):
        self.applied += self.pending
        self.pending = []
        return {}

    def discard_changes(self):
        self.pending = []
        return {}


def _normalize_as_firmware(wan_cfg: dict) -> None:
    """POST形式で書き込んだ値を、GET(参照)形式へ揃える。

    実機のファームウェアは POST と GET でスキーマが一部異なる。デモモードでも
    実機と同じ見え方になるよう、書き込み後にここで変換しておく。
      * healthcheck.method: POSTはオブジェクト {type, detail} / GETは文字列
      * healthcheck を有効化したとき、間隔などの既定値が入る
      * bandSelection: null は「バンド固定なし」= キー自体が無くなる
    """
    hc = wan_cfg.get("healthcheck")
    if isinstance(hc, dict):
        method = hc.get("method")
        if isinstance(method, dict):
            hc["method"] = method.get("type")
        if hc.get("enable"):
            hc.setdefault("timeout", 5)
            hc.setdefault("interval", 5)
            hc.setdefault("retry", 3)
            hc.setdefault("recovery", 3)
    for sim in _dig_list(wan_cfg, "cellular", "sim"):
        if sim.get("bandSelection", "keep") is None:
            sim.pop("bandSelection", None)


def _dig_list(obj: dict, *path: str) -> list[dict]:
    cur: Any = obj
    for key in path:
        cur = cur.get(key) if isinstance(cur, dict) else None
    return [x for x in cur if isinstance(x, dict)] if isinstance(cur, list) else []


def _deep_merge(base: dict, patch: dict) -> dict:
    """SIM配列(id一致でマージ)を含めて再帰的にマージする。"""
    for key, value in patch.items():
        if key == "sim" and isinstance(value, list) and isinstance(base.get("sim"), list):
            for new_sim in value:
                for existing in base["sim"]:
                    if existing.get("id") == new_sim.get("id"):
                        _deep_merge(existing, new_sim)
                        break
                else:
                    base["sim"].append(new_sim)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# 擬似データ本体
# ---------------------------------------------------------------------------


def _healthcheck(method="smartcheck", *, enable=True, interval=5, retry=3, recovery=3):
    return {
        "enable": enable,
        "method": method,
        "timeout": 5,
        "interval": interval,
        "retry": retry,
        "recovery": recovery,
    }


def _cellular_cfg(sims: list[dict], *, scheme="", preferred=1, network_mode="auto"):
    return {
        "useExternalAntenna": True,
        "simCardScheme": scheme,
        "preferredSim": preferred,
        "sim": sims,
        "__network_mode": network_mode,
    }


def _sim(sim_id, apn, *, roaming=False, bands=None, mobile_type="LTE", auto=False):
    entry: dict[str, Any] = {
        "id": sim_id,
        "operator": {"auto": auto, "apn": apn, "username": "", "password": ""},
        "roaming": {"enable": roaming},
        "mobileType": mobile_type,
        "authentication": None,
    }
    if bands:
        entry["bandSelection"] = bands
    return entry


def _band(name, *, rsrp, rsrq, sinr):
    return {"name": name, "channel": 1850, "signal": {"rsrp": rsrp, "rsrq": rsrq, "sinr": sinr}}


# ---- 1号機: MAX BR2 Pro(概ね基準どおり) --------------------------------

DEVICE_1 = {
    "firmware": {"1": {"version": "8.5.3 build 5539", "bootable": True, "inUse": False},
                 "2": {"version": "8.5.4 build 5612", "bootable": True, "inUse": True},
                 "order": [1, 2]},
    "wan_status": {
        "1": {
            "name": "Cellular 1", "enable": True, "message": "Connected", "uptime": 184320,
            "type": "cellular", "virtualType": "cellular", "priority": 1, "statusLed": "green",
            "ip": "10.132.44.21",
            "cellular": {
                "carrier": {"name": "NTT DOCOMO", "country": "Japan"},
                "mobileType": "LTE", "signalLevel": 4, "carrierAggregation": True,
                "roamingStatus": {"code": 1, "message": "home"},
                "rat": [{"name": "LTE", "band": [_band("B1", rsrp=-84, rsrq=-9, sinr=18),
                                                  _band("B3", rsrp=-88, rsrq=-10, sinr=15)]}],
                "sim": {"1": {"status": "In Use", "active": True, "apn": "lte.example.jp",
                              "iccid": "8981100000000000001"},
                        "2": {"status": "SIM Card Detected", "active": False,
                              "apn": "lte-b.example.jp", "iccid": "8981100000000000002"},
                        "order": [1, 2]},
            },
        },
        "2": {
            "name": "Cellular 2", "enable": True, "message": "Connected", "uptime": 184260,
            "type": "cellular", "virtualType": "cellular", "priority": 1, "statusLed": "green",
            "ip": "10.88.7.140",
            "cellular": {
                "carrier": {"name": "KDDI", "country": "Japan"},
                "mobileType": "LTE", "signalLevel": 3,
                "roamingStatus": {"code": 1, "message": "home"},
                "rat": [{"name": "LTE", "band": [_band("B18", rsrp=-93, rsrq=-11, sinr=11)]}],
                "sim": {"1": {"status": "In Use", "active": True, "apn": "au.example.jp",
                              "iccid": "8981200000000000001"},
                        "order": [1]},
            },
        },
        "3": {
            "name": "有線WAN", "enable": True, "message": "Standby", "uptime": 184400,
            "type": "ethernet", "virtualType": "ethernet", "priority": 2, "statusLed": "yellow",
            "ip": "192.168.1.24",
        },
        "order": [1, 2, 3],
    },
    "wan_config": {
        "1": {
            "name": "Cellular 1", "asLan": False, "enable": True, "active": True,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False, "priority": 1,
                           "dns": {"auto": True, "server": []},
                           "bandwidth": {"upload": {"value": 30000, "unit": "kbps"},
                                         "download": {"value": 120000, "unit": "kbps"}},
                           "hotStandby": {"enable": True},
                           "cellularModule": {"networkMode": "auto"}},
            "physical": {"type": "gobi", "speed": "Auto"},
            "healthcheck": _healthcheck("smartcheck", interval=5, retry=3, recovery=3),
            "bandwidthAllowanceMonitor": {"enable": True, "action": ["email"], "start": 1,
                                          "monthlyAllowance": {"value": 102400, "unit": "MB"}},
            "cellular": _cellular_cfg([_sim(1, "lte.example.jp"), _sim(2, "lte-b.example.jp")]),
        },
        "2": {
            "name": "Cellular 2", "asLan": False, "enable": True, "active": True,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False, "priority": 1,
                           "dns": {"auto": True, "server": []},
                           "bandwidth": {"upload": {"value": 20000, "unit": "kbps"},
                                         "download": {"value": 70000, "unit": "kbps"}},
                           "hotStandby": {"enable": True},
                           "cellularModule": {"networkMode": "auto"}},
            "physical": {"type": "gobi", "speed": "Auto"},
            "healthcheck": _healthcheck("smartcheck", interval=5, retry=3, recovery=3),
            "bandwidthAllowanceMonitor": {"enable": True, "action": ["email"], "start": 1,
                                          "monthlyAllowance": {"value": 51200, "unit": "MB"}},
            "cellular": _cellular_cfg([_sim(1, "au.example.jp"), _sim(2, "")]),
        },
        "3": {
            "name": "有線WAN", "asLan": False, "enable": True, "active": True,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False, "priority": 2,
                           "dns": {"auto": True, "server": []},
                           "bandwidth": {"upload": {"value": 100000, "unit": "kbps"},
                                         "download": {"value": 100000, "unit": "kbps"}},
                           "hotStandby": {"enable": True}},
            "physical": {"type": "ethernet", "speed": "Auto", "mtu": 1440},
            "healthcheck": _healthcheck("nslookup", interval=5, retry=3, recovery=3),
            "bandwidthAllowanceMonitor": {"enable": False},
        },
        "order": [1, 2, 3],
    },
    "wan_allowance": {
        "1": {"1": {"enable": True, "usage": 31200, "limit": 102400, "percent": 30,
                    "start": 1, "unit": "MB"},
              "order": [1]},
        "2": {"1": {"enable": True, "usage": 9800, "limit": 51200, "percent": 19,
                    "start": 1, "unit": "MB"},
              "order": [1]},
        "order": [1, 2],
    },
    "lan_status": {"0": {"ip": "192.168.50.1", "mask": 24},
                   "1": {"name": "配信機器", "ip": "192.168.60.1", "mask": 24, "vlanId": 60},
                   "order": [0, 1]},
    "pepvpn": {
        "profile": {"1": {"name": "本社データセンター", "master": True, "status": "CONNECTED",
                          "type": "l3", "peerCount": 1, "conflictCount": 0},
                    "order": [1], "siteId": "relay-01"},
        "peer": [{"serialNumber": "1927-XXXX-YYYY", "status": "CONNECTED", "name": "HQ-Balance",
                  "profileId": 1, "secure": True, "type": "l3", "peerId": "1-1"}],
    },
    "location": {"gps": True, "latitude": 35.6812, "longitude": 139.7671},
    "clients": {"1": {}, "2": {}, "3": {}, "4": {}, "order": [1, 2, 3, 4]},
}

# ---- 2号機: MAX Transit Pro Duo(意図的に不備あり) ----------------------

DEVICE_2 = {
    "firmware": {"1": {"version": "8.5.3 build 5539", "bootable": True, "inUse": True},
                 "order": [1]},
    "wan_status": {
        "1": {
            "name": "Cellular 1", "enable": True, "message": "Connected", "uptime": 43200,
            "type": "cellular", "virtualType": "cellular", "priority": 1, "statusLed": "green",
            "ip": "10.201.9.5",
            "cellular": {
                "carrier": {"name": "SoftBank", "country": "Japan"},
                "mobileType": "LTE", "signalLevel": 3,
                "roamingStatus": {"code": 1, "message": "home"},
                "rat": [{"name": "LTE", "band": [_band("B3", rsrp=-96, rsrq=-12, sinr=9)]}],
                "sim": {"1": {"status": "In Use", "active": True, "apn": "sb.example.jp",
                              "iccid": "8981300000000000001"},
                        "order": [1]},
            },
        },
        "2": {
            "name": "Cellular 2", "enable": True, "message": "Connected", "uptime": 43100,
            "type": "cellular", "virtualType": "cellular", "priority": 1, "statusLed": "yellow",
            "ip": "10.55.31.77",
            "cellular": {
                "carrier": {"name": "NTT DOCOMO", "country": "Japan"},
                "mobileType": "LTE", "signalLevel": 2,
                "roamingStatus": {"code": 1, "message": "home"},
                "rat": [{"name": "LTE", "band": [_band("B19", rsrp=-105, rsrq=-15, sinr=3)]}],
                "sim": {"1": {"status": "In Use", "active": True, "apn": "lte.example.jp",
                              "iccid": "8981400000000000001"},
                        "2": {"status": "SIM Card Detected", "active": False, "apn": "",
                              "iccid": "8981400000000000002"},
                        "order": [1, 2]},
            },
        },
        "3": {
            "name": "Wi-Fi WAN", "enable": False, "message": "Disabled", "uptime": 0,
            "type": "wireless", "virtualType": "wireless", "statusLed": "gray",
        },
        "order": [1, 2, 3],
    },
    "wan_config": {
        "1": {
            "name": "Cellular 1", "asLan": False, "enable": True, "active": True,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False, "priority": 1,
                           "dns": {"auto": True, "server": []},
                           "bandwidth": {"upload": {"value": 15000, "unit": "kbps"},
                                         "download": {"value": 45000, "unit": "kbps"}},
                           "hotStandby": {"enable": True},
                           "cellularModule": {"networkMode": "auto"}},
            "physical": {"type": "gobi", "speed": "Auto"},
            "healthcheck": _healthcheck("smartcheck", interval=5, retry=3, recovery=3),
            # 不備: 上限到達で自動切断する設定になっている(本番中の事故要因)
            "bandwidthAllowanceMonitor": {"enable": True, "action": ["email", "disconnect"],
                                          "start": 1,
                                          "monthlyAllowance": {"value": 51200, "unit": "MB"}},
            "cellular": _cellular_cfg([_sim(1, "sb.example.jp"), _sim(2, "")]),
        },
        "2": {
            "name": "Cellular 2", "asLan": False, "enable": True, "active": True,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False, "priority": 1,
                           "dns": {"auto": True, "server": []},
                           # 不備: 申告帯域が実測(約18Mbps)から大きく乖離
                           "bandwidth": {"upload": {"value": 50000, "unit": "kbps"},
                                         "download": {"value": 150000, "unit": "kbps"}},
                           # 不備: 待機時の接続維持が無効
                           "hotStandby": {"enable": False},
                           "cellularModule": {"networkMode": "auto"}},
            "physical": {"type": "gobi", "speed": "Auto"},
            # 不備: ヘルスチェックが無効
            "healthcheck": {"enable": False},
            "bandwidthAllowanceMonitor": {"enable": True, "action": ["email"], "start": 1,
                                          "monthlyAllowance": {"value": 51200, "unit": "MB"}},
            # 不備: SIM B のAPNが未設定 / バンド固定が残っている
            "cellular": _cellular_cfg(
                [_sim(1, "lte.example.jp", bands=["B19"]), _sim(2, "")],
            ),
        },
        "3": {
            "name": "Wi-Fi WAN", "asLan": False, "enable": False, "active": False,
            "connection": {"method": "dhcp", "mode": "NAT", "icmpPing": False,
                           "dns": {"auto": True, "server": []},
                           "hotStandby": {"enable": False}},
            "physical": {"type": "wireless", "speed": "Auto"},
            "healthcheck": {"enable": False},
            "bandwidthAllowanceMonitor": {"enable": False},
        },
        "order": [1, 2, 3],
    },
    "wan_allowance": {
        "1": {"1": {"enable": True, "usage": 48900, "limit": 51200, "percent": 95,
                    "start": 1, "unit": "MB"},
              "order": [1]},
        "2": {"1": {"enable": True, "usage": 12400, "limit": 51200, "percent": 24,
                    "start": 1, "unit": "MB"},
              "order": [1]},
        "order": [1, 2],
    },
    "lan_status": {"0": {"ip": "192.168.51.1", "mask": 24}, "order": [0]},
    "pepvpn": {
        "profile": {"1": {"name": "本社データセンター", "master": True, "status": "CONNECTED",
                          "type": "l3", "peerCount": 1, "conflictCount": 0},
                    "order": [1], "siteId": "relay-02"},
        "peer": [{"serialNumber": "2833-XXXX-YYYY", "status": "CONNECTED", "name": "HQ-Balance",
                  "profileId": 1, "secure": True, "type": "l3", "peerId": "1-2"}],
    },
    "location": {"gps": True, "latitude": 34.6937, "longitude": 135.5023},
    "clients": {"1": {}, "2": {}, "order": [1, 2]},
}


DEMO_DEVICES = [
    {
        "id": "relay-01",
        "label": "中継車1号(デモ)",
        "model": "MAX BR2 Pro",
        "host": "192.168.50.1",
        "profile": "live-broadcast",
        "_dataset": DEVICE_1,
    },
    {
        "id": "relay-02",
        "label": "中継車2号(デモ)",
        "model": "MAX Transit Pro Duo",
        "host": "192.168.51.1",
        "profile": "live-broadcast",
        "_dataset": DEVICE_2,
    },
]


def demo_config() -> dict:
    """デモモード用の config 相当のデータを返す。"""
    import copy

    return {
        "demo": True,
        "allow_write": True,
        "devices": copy.deepcopy(DEMO_DEVICES),
    }
