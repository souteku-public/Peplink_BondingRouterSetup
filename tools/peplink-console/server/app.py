"""HTTPサーバー本体(標準ライブラリのみ)。

役割:
  * /            … Web UI(web/ 配下の静的ファイル)
  * /api/...     … ブラウザ向けの内部API(機器のRouter APIは常にサーバー側から呼ぶ)

機器の認証情報はブラウザに渡さない。ブラウザ ⇄ 本サーバー ⇄ 機器 の3層構成。
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import catalog as catalog_mod
from .audit import RuleError, run_audit
from .collector import collect
from .demo import DemoClient
from .peplink import PeplinkClient, PeplinkError

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


class Store:
    """機器スナップショットとプロファイルを保持する。"""

    def __init__(self, config: dict, profiles: dict[str, dict]):
        self.config = config
        self.profiles = profiles
        self.snapshots: dict[str, dict] = {}
        self.values: dict[str, dict] = {}
        self.pending: dict[str, list[dict]] = {}
        self.demo_clients: dict[str, DemoClient] = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ 機器
    @property
    def demo(self) -> bool:
        return bool(self.config.get("demo"))

    @property
    def allow_write(self) -> bool:
        return bool(self.config.get("allow_write"))

    def devices(self) -> list[dict]:
        return self.config.get("devices") or []

    def device_conf(self, device_id: str) -> dict | None:
        for dev in self.devices():
            if str(dev.get("id")) == str(device_id):
                return dev
        return None

    def client_for(self, dev: dict):
        """機器1台分のクライアントを返す(デモモードでは擬似クライアント)。"""
        if self.demo:
            dev_id = str(dev.get("id"))
            if dev_id not in self.demo_clients:
                self.demo_clients[dev_id] = DemoClient(
                    dev["_dataset"], can_write=self.allow_write
                )
            return self.demo_clients[dev_id]
        return PeplinkClient(
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

    # ------------------------------------------------------------- 吸い上げ
    def refresh(self, device_id: str | None = None) -> list[dict]:
        targets = [d for d in self.devices() if device_id in (None, str(d.get("id")))]
        out = []
        for dev in targets:
            snapshot = collect(self.client_for(dev), dev)
            with self.lock:
                self.snapshots[str(dev["id"])] = snapshot
                self.values[str(dev["id"])] = catalog_mod.read_values(snapshot)
            out.append(snapshot)
        return out

    def ensure(self, device_id: str) -> dict:
        if device_id not in self.snapshots:
            self.refresh(device_id)
        return self.snapshots.get(device_id, {})


# ---------------------------------------------------------------------------
# 画面向けのデータ整形
# ---------------------------------------------------------------------------


def _wan_view(wan: dict) -> dict:
    sig = wan.get("signal") or {}
    usage = wan.get("usage") or {}
    cfg = wan.get("config") or {}
    return {
        "id": wan["id"],
        "name": wan.get("name"),
        "type": wan.get("type"),
        "enable": wan.get("enable"),
        "priority": wan.get("priority"),
        "status_led": wan.get("status_led"),
        "message": wan.get("message"),
        "ip": wan.get("ip"),
        "uptime": wan.get("uptime"),
        "carrier": sig.get("carrier"),
        "mobile_type": sig.get("mobile_type"),
        "roaming": sig.get("roaming"),
        "bands": sig.get("bands"),
        "rsrp": sig.get("rsrp"),
        "rsrq": sig.get("rsrq"),
        "sinr": sig.get("sinr"),
        "rssi": sig.get("rssi"),
        "level": sig.get("level"),
        "ssid": sig.get("ssid"),
        "strength": sig.get("strength"),
        "sims": wan.get("sims"),
        "usage": {
            "usage": usage.get("usage"),
            "limit": usage.get("limit"),
            "percent": usage.get("percent"),
        }
        if usage
        else None,
        "healthcheck": (cfg.get("healthcheck") or {}).get("method")
        if (cfg.get("healthcheck") or {}).get("enable")
        else None,
        "hot_standby": (
            (cfg.get("connection") or {}).get("hotStandby") or {}
        ).get("enable"),
    }


def _device_view(snapshot: dict, audit_summary: dict | None) -> dict:
    return {
        "id": snapshot.get("id"),
        "label": snapshot.get("label"),
        "model": snapshot.get("model"),
        "host": snapshot.get("host"),
        "firmware": (snapshot.get("device") or {}).get("firmware"),
        "online": snapshot.get("online"),
        "can_write": snapshot.get("can_write"),
        "collected_at": snapshot.get("collected_at"),
        "errors": snapshot.get("errors"),
        "wans": [_wan_view(w) for w in snapshot.get("wans", [])],
        "tunnels": snapshot.get("tunnels"),
        "lans": snapshot.get("lans"),
        "location": snapshot.get("location"),
        "client_count": snapshot.get("client_count"),
        "audit": audit_summary,
    }


def _audit_for(store: Store, device_id: str) -> dict | None:
    snapshot = store.snapshots.get(device_id)
    if not snapshot:
        return None
    dev = store.device_conf(device_id) or {}
    profile_name = dev.get("profile")
    profile = store.profiles.get(profile_name)
    if not profile:
        return None
    try:
        return run_audit(snapshot, store.values.get(device_id, {}), profile)
    except RuleError as exc:
        return {"error": f"プロファイル {profile_name} の記述に誤りがあります: {exc}"}


# ---------------------------------------------------------------------------
# 変更の適用
# ---------------------------------------------------------------------------


def _merge_entries(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """WAN IDごとに設定変更をまとめ、優先度変更は別リストに分ける。"""
    from .demo import _deep_merge

    wan_entries: dict[int, dict] = {}
    priority_entries: list[dict] = []
    for entry in entries:
        if entry.get("__priority__"):
            priority_entries.append(
                {"connId": entry["connId"], "priority": entry["priority"]}
            )
            continue
        wan_id = entry.get("id")
        if wan_id is None:
            continue
        target = wan_entries.setdefault(wan_id, {"id": wan_id})
        _deep_merge(target, {k: v for k, v in entry.items() if k != "id"})
    return list(wan_entries.values()), priority_entries


def _build_changes(store: Store, device_id: str, changes: list[dict]) -> list[dict]:
    """UIから来た {key, value} のリストを Router API のペイロードへ変換する。"""
    snapshot = store.snapshots.get(device_id) or {}
    index = catalog_mod.catalog_index(snapshot)
    payloads: list[dict] = []
    for change in changes:
        key = change.get("key")
        item = index.get(key)
        if item is None:
            raise ValueError(f"未知の設定キーです: {key}")
        if not item.writable:
            reason = item.manual or "Router API で変更できない項目です"
            raise ValueError(f"{item.label} は変更できません({reason})")
        payloads.append(item.write(change.get("value")))
    return payloads


# ---------------------------------------------------------------------------
# ルーティング
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "PeplinkConsole"
    store: Store  # ThreadingHTTPServer 側で注入する

    def log_message(self, fmt: str, *args: Any) -> None:  # アクセスログを簡潔に
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    # ------------------------------------------------------------ 応答ヘルパー
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"リクエストのJSONが不正です: {exc}") from exc

    def _serve_static(self, path: str) -> None:
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            self.send_error(404, "Not Found")
            return
        ctype, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler の規約)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        store = self.store

        try:
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not path.startswith("/api/"):
                self._serve_static(path)
                return

            if path == "/api/meta":
                self._send_json(
                    {
                        "demo": store.demo,
                        "allow_write": store.allow_write,
                        "devices": [
                            {
                                "id": d.get("id"),
                                "label": d.get("label"),
                                "model": d.get("model"),
                                "host": d.get("host"),
                                "profile": d.get("profile"),
                            }
                            for d in store.devices()
                        ],
                        "profiles": [
                            {
                                "name": name,
                                "title": prof.get("title") or name,
                                "description": prof.get("description"),
                            }
                            for name, prof in store.profiles.items()
                        ],
                    }
                )
                return

            if path == "/api/dashboard":
                if query.get("refresh", ["0"])[0] in ("1", "true"):
                    store.refresh()
                elif not store.snapshots:
                    store.refresh()
                devices = []
                for dev in store.devices():
                    dev_id = str(dev["id"])
                    snapshot = store.snapshots.get(dev_id)
                    if snapshot is None:
                        continue
                    audit = _audit_for(store, dev_id)
                    summary = None
                    if audit and "counts" in audit:
                        summary = {
                            "compliance": audit["compliance"],
                            "counts": audit["counts"],
                            "profile": audit["profile"],
                        }
                    elif audit:
                        summary = {"error": audit.get("error")}
                    devices.append(_device_view(snapshot, summary))
                self._send_json({"devices": devices})
                return

            if path == "/api/audit":
                device_id = query.get("device", [None])[0]
                if not device_id:
                    self._send_error_json("device パラメータが必要です")
                    return
                store.ensure(device_id)
                profile_name = query.get("profile", [None])[0]
                if profile_name:
                    dev = store.device_conf(device_id)
                    if dev is None:
                        self._send_error_json("該当する機器がありません", 404)
                        return
                    dev["profile"] = profile_name
                result = _audit_for(store, device_id)
                if result is None:
                    self._send_error_json("プロファイルが見つかりません", 404)
                    return
                self._send_json(result)
                return

            if path == "/api/settings":
                device_id = query.get("device", [None])[0]
                if not device_id:
                    self._send_error_json("device パラメータが必要です")
                    return
                snapshot = store.ensure(device_id)
                values = store.values.get(device_id, {})
                items = catalog_mod.build_catalog(snapshot)
                rows = []
                for item in items:
                    row = item.to_json()
                    row["value"] = values.get(item.key) if item.readable else None
                    rows.append(row)
                self._send_json(
                    {
                        "device": {
                            "id": snapshot.get("id"),
                            "label": snapshot.get("label"),
                            "model": snapshot.get("model"),
                            "collected_at": snapshot.get("collected_at"),
                            "can_write": snapshot.get("can_write"),
                        },
                        "items": rows,
                    }
                )
                return

            if path == "/api/pending":
                device_id = query.get("device", [None])[0]
                self._send_json({"pending": store.pending.get(str(device_id), [])})
                return

            self._send_error_json("不明なエンドポイントです", 404)
        except PeplinkError as exc:
            self._send_error_json(f"機器との通信に失敗しました: {exc}", 502)
        except Exception as exc:  # noqa: BLE001 予期しない例外も画面に返す
            traceback.print_exc()
            self._send_error_json(f"サーバー内部エラー: {exc}", 500)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store = self.store

        try:
            body = self._read_json()

            if path == "/api/refresh":
                snapshots = store.refresh(body.get("device"))
                self._send_json({"refreshed": [s.get("id") for s in snapshots]})
                return

            if path == "/api/pending":
                # 変更を保留リストへ積む(まだ機器には送らない)
                device_id = str(body.get("device") or "")
                changes = body.get("changes") or []
                if not store.device_conf(device_id):
                    self._send_error_json("該当する機器がありません", 404)
                    return
                store.ensure(device_id)
                _validate = _build_changes(store, device_id, changes)  # 変換可否の事前検証
                current = store.pending.setdefault(device_id, [])
                for change in changes:
                    current[:] = [c for c in current if c.get("key") != change.get("key")]
                    current.append(change)
                self._send_json({"pending": current})
                return

            if path == "/api/pending/clear":
                device_id = str(body.get("device") or "")
                store.pending[device_id] = []
                self._send_json({"pending": []})
                return

            if path == "/api/apply":
                device_id = str(body.get("device") or "")
                dev = store.device_conf(device_id)
                if dev is None:
                    self._send_error_json("該当する機器がありません", 404)
                    return
                if not store.allow_write:
                    self._send_error_json(
                        "書き込みが無効です。config.yaml の allow_write を true にしてください(既定は参照専用)",
                        403,
                    )
                    return
                changes = store.pending.get(device_id) or []
                if not changes:
                    self._send_error_json("保留中の変更がありません")
                    return

                payloads = _build_changes(store, device_id, changes)
                wan_entries, priority_entries = _merge_entries(payloads)

                client = store.client_for(dev)
                if not store.demo:
                    client.login()
                    if not client.can_write:
                        self._send_error_json(
                            "この認証情報には変更権限がありません(Read-Onlyトークン/ユーザーです)", 403
                        )
                        return
                result: dict[str, Any] = {"saved": [], "applied": False}
                if wan_entries:
                    client.update_wan_config(wan_entries)
                    result["saved"].append(f"WAN設定 {len(wan_entries)}件")
                if priority_entries:
                    client.update_wan_priority(priority_entries)
                    result["saved"].append(f"優先度 {len(priority_entries)}件")
                apply_res = client.apply_changes()
                result["applied"] = True
                if isinstance(apply_res, dict) and apply_res.get("warning"):
                    result["warning"] = apply_res["warning"]
                if not store.demo:
                    client.logout()

                store.pending[device_id] = []
                store.refresh(device_id)
                self._send_json(result)
                return

            if path == "/api/discard":
                device_id = str(body.get("device") or "")
                dev = store.device_conf(device_id)
                if dev is None:
                    self._send_error_json("該当する機器がありません", 404)
                    return
                client = store.client_for(dev)
                if not store.demo:
                    client.login()
                client.discard_changes()
                if not store.demo:
                    client.logout()
                store.pending[device_id] = []
                self._send_json({"discarded": True})
                return

            self._send_error_json("不明なエンドポイントです", 404)
        except ValueError as exc:
            self._send_error_json(str(exc), 400)
        except PeplinkError as exc:
            self._send_error_json(f"機器との通信に失敗しました: {exc}", 502)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_error_json(f"サーバー内部エラー: {exc}", 500)


def serve(config: dict, profiles: dict[str, dict], host: str, port: int) -> None:
    store = Store(config, profiles)
    handler = type("BoundHandler", (Handler,), {"store": store})
    httpd = ThreadingHTTPServer((host, port), handler)
    mode = "デモモード(実機に接続しません)" if store.demo else "実機モード"
    write = "変更可(allow_write: true)" if store.allow_write else "参照専用(allow_write: false)"
    print(f"Peplink 設定管理コンソール — {mode} / {write}")
    print(f"機器数: {len(store.devices())}  プロファイル: {', '.join(profiles) or 'なし'}")
    print(f"起動しました: http://{host}:{port}/  (停止は Ctrl+C)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    finally:
        httpd.server_close()
