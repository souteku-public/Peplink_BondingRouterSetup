"""Peplink Router API の最小クライアント(回線切替用)。

回線ごとの測定モードで、対象WANだけを優先度1にして他をバックアップへ落とし、
測定後に元の優先度へ復元するためだけに使う。設定管理コンソールとは独立して
動作するよう、必要最小限を本アプリ内に持つ。

使用エンドポイント(公式 Router API 8.5.0):
  POST /api/login                              認証
  GET  /api/status.wan.connection              WAN一覧と現在の優先度・状態
  POST /api/config.wan.connection.priority     優先度変更(instantActive=即時反映)
"""

from __future__ import annotations

import http.cookiejar
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any


class RouterError(Exception):
    pass


class RouterClient:
    def __init__(self, conf: dict):
        self.host = conf["host"]
        port = conf.get("port")
        scheme = conf.get("scheme", "https")
        netloc = self.host if not port else f"{self.host}:{port}"
        self.base = f"{scheme}://{netloc}"
        self.conf = conf
        self.timeout = float(conf.get("timeout", 15))

        handlers: list[Any] = [
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.ProxyHandler({}),  # LAN内機器のため環境プロキシは使わない
        ]
        if scheme == "https":
            ctx = ssl.create_default_context()
            if not conf.get("verify_tls", False):
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)
        self._token: str | None = None

    def _call(self, method: str, endpoint: str, payload: dict | None = None) -> Any:
        url = f"{self.base}/api/{endpoint}"
        if self._token:
            url += f"?accessToken={self._token}"
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as res:
                body = json.loads(res.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as exc:
            raise RouterError(f"{self.host} に接続できません: {getattr(exc, 'reason', exc)}") from exc
        except json.JSONDecodeError as exc:
            raise RouterError(
                "応答をJSONとして解析できません(APIアクセスが無効の可能性。System > Admin Security を確認)"
            ) from exc
        if body.get("stat") != "ok":
            raise RouterError(f"{endpoint}: {body.get('message') or body.get('code') or 'fail'}")
        return body.get("response")

    # ------------------------------------------------------------------ 操作

    def login(self) -> None:
        if self.conf.get("client_id"):
            res = self._call("POST", "auth.token.grant", {
                "clientId": self.conf["client_id"],
                "clientSecret": self.conf["client_secret"],
            })
            self._token = (res or {}).get("accessToken")
            if not self._token:
                raise RouterError("アクセストークンを取得できませんでした")
        else:
            res = self._call("POST", "login", {
                "username": self.conf.get("username", "admin"),
                "password": self.conf.get("password", ""),
            })
            if not (res or {}).get("permission", {}).get("POST"):
                raise RouterError(
                    "この認証情報には変更権限がありません(回線切替には Read-Write 権限が必要です)"
                )

    def wan_list(self) -> list[dict]:
        """WAN一覧: [{id, name, type, enable, priority, message}] を返す。"""
        res = self._call("GET", "status.wan.connection") or {}
        order = res.get("order") or [k for k in res.keys() if k != "order"]
        out = []
        for wan_id in order:
            entry = res.get(str(wan_id))
            if isinstance(entry, dict):
                out.append({
                    "id": int(wan_id),
                    "name": entry.get("name") or f"WAN {wan_id}",
                    "type": entry.get("virtualType") or entry.get("type"),
                    "enable": entry.get("enable"),
                    "priority": entry.get("priority"),
                    "message": entry.get("message"),
                })
        return out

    def set_priorities(self, entries: list[dict]) -> None:
        """[{connId, priority}] を即時反映(instantActive)で適用する。"""
        self._call("POST", "config.wan.connection.priority",
                   {"instantActive": True, "list": entries})

    def wait_connected(self, wan_id: int, timeout: float = 60.0) -> bool:
        """指定WANが Connected になるまで待つ。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for wan in self.wan_list():
                if wan["id"] == wan_id and (wan.get("message") or "").lower().startswith("connected"):
                    return True
            time.sleep(2)
        return False


class PrioritySnapshot:
    """現在の優先度を保存し、確実に復元するためのコンテキスト。"""

    def __init__(self, client: RouterClient):
        self.client = client
        self.original: list[dict] = []

    def capture(self) -> list[dict]:
        wans = self.client.wan_list()
        self.original = [
            {"connId": w["id"], "priority": w["priority"]}
            for w in wans
            if w.get("enable") and w.get("priority")
        ]
        if not self.original:
            raise RouterError("有効なWANが見つかりません(全WANが無効/切断の可能性)")
        return wans

    def isolate(self, target_id: int) -> None:
        """対象WANをP1、他の有効WANをP2に落とす(無効化はしない=接続は維持)。"""
        entries = [
            {"connId": e["connId"], "priority": 1 if e["connId"] == target_id else 2}
            for e in self.original
        ]
        self.client.set_priorities(entries)

    def restore(self) -> None:
        if self.original:
            self.client.set_priorities(self.original)
