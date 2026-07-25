"""機器から状態・設定を吸い上げて、UIと監査エンジンが扱う形へ正規化する。"""

from __future__ import annotations

import datetime
from typing import Any

from .peplink import PeplinkClient, PeplinkError

# status.wan.connection / config.wan.connection のレスポンスは
# {"1": {...}, "2": {...}, "order": [1, 2]} という形なので order を使って並べる。


def _ordered(resp: Any) -> list[tuple[int, dict]]:
    if not isinstance(resp, dict):
        return []
    order = resp.get("order")
    keys = [str(k) for k in order] if isinstance(order, list) else [
        k for k in resp.keys() if k != "order"
    ]
    out: list[tuple[int, dict]] = []
    for key in keys:
        value = resp.get(key)
        if isinstance(value, dict):
            try:
                out.append((int(key), value))
            except (TypeError, ValueError):
                continue
    return out


def _signal_from_status(wan_status: dict) -> dict:
    """WAN状態から電波指標(RSRP/RSRQ/SINR/RSSI)を取り出す。

    セルラーは rat[].band[].signal または band[].signal の下、
    Wi-Fi WAN は wireless.signal.strength に入っている。
    """
    detail = wan_status.get("cellular") or wan_status.get("gobi") or wan_status.get("modem")
    out: dict[str, Any] = {}
    if isinstance(detail, dict):
        out["level"] = detail.get("signalLevel")
        bands: list[dict] = []
        for rat in detail.get("rat") or []:
            if isinstance(rat, dict):
                bands += [b for b in (rat.get("band") or []) if isinstance(b, dict)]
        bands += [b for b in (detail.get("band") or []) if isinstance(b, dict)]
        names = [b.get("name") for b in bands if b.get("name")]
        if names:
            out["bands"] = names
        for band in bands:
            sig = band.get("signal")
            if isinstance(sig, dict):
                for field in ("rsrp", "rsrq", "sinr", "rssi", "snr"):
                    if out.get(field) is None and sig.get(field) is not None:
                        out[field] = sig[field]
        carrier = detail.get("carrier")
        if isinstance(carrier, dict):
            out["carrier"] = carrier.get("name")
        out["mobile_type"] = detail.get("mobileType") or detail.get("network")
        roaming = detail.get("roamingStatus")
        if isinstance(roaming, dict):
            out["roaming"] = roaming.get("message")
    wireless = wan_status.get("wireless")
    if isinstance(wireless, dict):
        sig = wireless.get("signal") or {}
        if isinstance(sig, dict) and sig.get("strength") is not None:
            out["strength"] = sig["strength"]
        out["ssid"] = wireless.get("ssid")
    return out


def _sims_from_status(wan_status: dict) -> tuple[list[dict], list[int]]:
    detail = wan_status.get("cellular") or wan_status.get("gobi") or {}
    sim_group = detail.get("sim") if isinstance(detail, dict) else None
    sims: list[dict] = []
    ids: list[int] = []
    for sim_id, sim in _ordered(sim_group):
        ids.append(sim_id)
        sims.append(
            {
                "id": sim_id,
                "slot": "A" if sim_id == 1 else "B",
                "status": sim.get("status"),
                "active": sim.get("active"),
                "apn": sim.get("apn"),
                "iccid": sim.get("iccid"),
            }
        )
    return sims, ids


def collect(client: PeplinkClient, device_conf: dict) -> dict:
    """1台の機器から必要な情報をまとめて取得する。

    取得できなかった項目は errors に記録し、取れた分だけで画面を作れるようにする。
    """
    snapshot: dict[str, Any] = {
        "id": device_conf.get("id"),
        "label": device_conf.get("label") or device_conf.get("host"),
        "model": device_conf.get("model"),  # APIから取得できないため設定ファイルの値を使う
        "host": device_conf.get("host"),
        "collected_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "online": False,
        "can_write": False,
        "errors": [],
        "device": {},
        "wans": [],
        "tunnels": {"profiles": [], "peers": []},
        "lans": [],
        "location": None,
        "client_count": None,
    }

    def guard(name: str, fn):
        try:
            return fn()
        except PeplinkError as exc:
            snapshot["errors"].append({"endpoint": name, "message": str(exc)})
            return None

    try:
        client.login()
    except PeplinkError as exc:
        snapshot["errors"].append({"endpoint": "login", "message": str(exc)})
        return snapshot

    snapshot["online"] = True
    snapshot["can_write"] = client.can_write

    # --- ファームウェア(使用中のものを採用)
    fw = guard("info.firmware", client.firmware)
    for _fw_id, entry in _ordered(fw):
        if entry.get("inUse"):
            snapshot["device"]["firmware"] = entry.get("version")
    if "firmware" not in snapshot["device"]:
        first = next((e for _i, e in _ordered(fw)), None)
        if first:
            snapshot["device"]["firmware"] = first.get("version")

    # --- WAN 状態 + 設定 + データ使用量
    status = guard("status.wan.connection", client.wan_status) or {}
    config = guard("config.wan.connection", client.wan_config) or {}
    allowance = guard("status.wan.connection.allowance", client.wan_allowance) or {}
    config_by_id = {wid: cfg for wid, cfg in _ordered(config)}

    for wan_id, st in _ordered(status):
        cfg = config_by_id.get(wan_id, {})
        sims, sim_ids = _sims_from_status(st)
        usage = allowance.get(str(wan_id)) if isinstance(allowance, dict) else None
        # セルラーは SIM ごとの入れ子、それ以外は直接 Allowance_Obj
        usage_obj = None
        if isinstance(usage, dict):
            if "usage" in usage:
                usage_obj = usage
            else:
                for _sid, entry in _ordered(usage):
                    if entry.get("enable"):
                        usage_obj = entry
                        break
                if usage_obj is None:
                    usage_obj = next((e for _s, e in _ordered(usage)), None)

        snapshot["wans"].append(
            {
                "id": wan_id,
                "name": st.get("name") or cfg.get("name") or f"WAN {wan_id}",
                "type": st.get("virtualType") or st.get("type"),
                "enable": st.get("enable"),
                "priority": st.get("priority"),
                "status_led": st.get("statusLed"),
                "message": st.get("message"),
                "uptime": st.get("uptime"),
                "ip": st.get("ip"),
                "signal": _signal_from_status(st),
                "sims": sims,
                "sim_ids": sim_ids or [1, 2],
                "usage": usage_obj,
                "config": cfg,
            }
        )

    # --- SpeedFusion(参照のみ)
    pepvpn = guard("status.pepvpn", client.pepvpn_status)
    if isinstance(pepvpn, dict):
        profiles = []
        for pid, prof in _ordered(pepvpn.get("profile")):
            profiles.append(
                {
                    "id": pid,
                    "name": prof.get("name"),
                    "status": prof.get("status"),
                    "type": prof.get("type"),
                    "peer_count": prof.get("peerCount"),
                }
            )
        peers = []
        for peer in pepvpn.get("peer") or []:
            if isinstance(peer, dict):
                peers.append(
                    {
                        "name": peer.get("name"),
                        "status": peer.get("status"),
                        "profile_id": peer.get("profileId"),
                        "serial": peer.get("serialNumber"),
                        "secure": peer.get("secure"),
                    }
                )
        site_id = (pepvpn.get("profile") or {}).get("siteId")
        snapshot["tunnels"] = {"profiles": profiles, "peers": peers, "site_id": site_id}

    # --- LAN(参照のみ)
    lan = guard("status.lan.profile", client.lan_status)
    for lan_id, entry in _ordered(lan):
        snapshot["lans"].append(
            {
                "id": lan_id,
                "name": entry.get("name") or ("既定LAN" if lan_id == 0 else f"VLAN {lan_id}"),
                "ip": entry.get("ip"),
                "mask": entry.get("mask"),
                "vlan_id": entry.get("vlanId"),
            }
        )

    # --- 位置情報・クライアント数(任意項目。失敗しても致命ではない)
    loc = guard("info.location", client.location)
    if isinstance(loc, dict):
        snapshot["location"] = {
            "gps": loc.get("gps"),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
        }
    clients = guard("status.client", client.clients)
    if isinstance(clients, dict):
        snapshot["client_count"] = len(
            [k for k in clients.keys() if k not in ("order", "summary")]
        )
    elif isinstance(clients, list):
        snapshot["client_count"] = len(clients)

    client.logout()
    return snapshot
