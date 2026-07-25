"""Peplink Router API クライアント。

公式ドキュメント "Peplink Router API Documentation 8.5.0" に準拠。
    https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf

認証は2方式に対応する。
  * admin: /api/login にユーザー名/パスワードを送り、Cookie(bauth)でセッションを維持する
  * token: 事前に発行した clientId/clientSecret から /api/auth.token.grant でアクセストークンを取得する

標準ライブラリのみを使用する(外部HTTPライブラリ不要)。
"""

from __future__ import annotations

import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PeplinkError(Exception):
    """Router API 呼び出しが失敗したときに投げる例外。"""

    def __init__(self, message: str, code: int | None = None, endpoint: str | None = None):
        self.code = code
        self.endpoint = endpoint
        super().__init__(message)


class PeplinkClient:
    """1台の Peplink 機器に対する Router API クライアント。

    使用例::

        client = PeplinkClient("192.168.50.1", username="admin", password="...")
        client.login()
        wan = client.get("status.wan.connection")
    """

    def __init__(
        self,
        host: str,
        *,
        port: int | None = None,
        scheme: str = "https",
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        verify_tls: bool = False,
        timeout: float = 15.0,
        use_proxy: bool = False,
    ):
        self.host = host
        self.port = port
        self.scheme = scheme
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret
        self.verify_tls = verify_tls
        self.timeout = timeout

        self._access_token: str | None = None
        self._permission: dict[str, int] = {}

        self._cookiejar = http.cookiejar.CookieJar()
        handlers: list[Any] = [urllib.request.HTTPCookieProcessor(self._cookiejar)]
        if not use_proxy:
            # 機器はLAN内にあるため、環境変数 HTTP(S)_PROXY を既定で無視する。
            # (社内プロキシ設定があると機器へ到達できなくなるため)
            handlers.append(urllib.request.ProxyHandler({}))
        if scheme == "https":
            ctx = ssl.create_default_context()
            if not verify_tls:
                # 機器の管理画面は自己署名証明書のため、既定では検証しない。
                # 社内CAで署名した証明書を入れている場合は verify_tls: true を設定する。
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------ 基本

    @property
    def base_url(self) -> str:
        netloc = self.host if self.port is None else f"{self.host}:{self.port}"
        return f"{self.scheme}://{netloc}"

    @property
    def can_write(self) -> bool:
        """このセッションが設定変更(POST)を許可されているか。"""
        return bool(self._permission.get("POST"))

    def _url(self, endpoint: str, params: dict[str, Any] | None = None) -> str:
        query: dict[str, Any] = dict(params or {})
        if self._access_token:
            query["accessToken"] = self._access_token
        url = f"{self.base_url}/api/{endpoint}"
        if query:
            # 配列パラメータ(id=1&id=2 形式)にも対応する
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        return url

    def _request(self, method: str, endpoint: str, *, params=None, payload=None) -> Any:
        url = self._url(endpoint, params)
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as res:
                raw = res.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise PeplinkError(
                f"HTTP {exc.code} {exc.reason}: {body[:300]}", endpoint=endpoint
            ) from exc
        except urllib.error.URLError as exc:
            raise PeplinkError(
                f"{self.host} に接続できません: {exc.reason}", endpoint=endpoint
            ) from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            # APIが無効な場合、管理画面のHTMLが返ってくることがある
            hint = ""
            if raw.lstrip().startswith("<"):
                hint = " (HTMLが返却されました。System > Admin Security でAPIアクセスが"
                hint += "有効か、URL/ポートが正しいか確認してください)"
            raise PeplinkError(f"JSONを解析できません{hint}", endpoint=endpoint) from exc

        if body.get("stat") != "ok":
            raise PeplinkError(
                body.get("message") or "APIがfailを返しました",
                code=body.get("code"),
                endpoint=endpoint,
            )
        return body.get("response")

    def get(self, endpoint: str, **params: Any) -> Any:
        """Router API の GET エンドポイントを呼ぶ(状態・設定の取得)。"""
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        """Router API の POST エンドポイントを呼ぶ(設定変更・コマンド実行)。"""
        return self._request("POST", endpoint, payload=payload or {})

    # ------------------------------------------------------------------ 認証

    def login(self) -> dict[str, int]:
        """認証を行い、許可された権限(GET/POST)を返す。"""
        if self.client_id and self.client_secret:
            res = self._request(
                "POST",
                "auth.token.grant",
                payload={"clientId": self.client_id, "clientSecret": self.client_secret},
            )
            token = (res or {}).get("accessToken") or (res or {}).get("access_token")
            if not token:
                raise PeplinkError("アクセストークンを取得できませんでした", endpoint="auth.token.grant")
            self._access_token = token
            # トークンのスコープから権限を推定する。api.read-only は参照のみ。
            scope = (res or {}).get("scope", "")
            self._permission = {"GET": 1, "POST": 0 if "read-only" in str(scope) else 1}
        elif self.username is not None:
            res = self._request(
                "POST",
                "login",
                payload={"username": self.username, "password": self.password or ""},
            )
            self._permission = dict((res or {}).get("permission") or {"GET": 1, "POST": 0})
        else:
            raise PeplinkError("認証情報(username/password または clientId/clientSecret)がありません")
        return self._permission

    def logout(self) -> None:
        """セッションを明示的に破棄する(トークン認証時は何もしない)。"""
        if self._access_token:
            return
        try:
            self.post("logout")
        except PeplinkError:
            pass

    def __enter__(self) -> "PeplinkClient":
        self.login()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.logout()

    # ------------------------------------------------- よく使うエンドポイント

    def firmware(self) -> Any:
        return self.get("info.firmware")

    def wan_status(self) -> Any:
        return self.get("status.wan.connection")

    def wan_config(self) -> Any:
        return self.get("config.wan.connection")

    def wan_allowance(self) -> Any:
        return self.get("status.wan.connection.allowance")

    def lan_status(self) -> Any:
        return self.get("status.lan.profile")

    def pepvpn_status(self) -> Any:
        return self.get("status.pepvpn", infoType=["profile", "peer"])

    def ssid_config(self) -> Any:
        return self.get("config.ssid.profile")

    def location(self) -> Any:
        return self.get("info.location")

    def clients(self) -> Any:
        return self.get("status.client")

    def update_wan_config(self, entries: list[dict[str, Any]]) -> Any:
        """WAN設定を更新する(保留状態。反映には apply_changes が必要)。"""
        return self.post("config.wan.connection", {"action": "update", "list": entries})

    def update_wan_priority(self, entries: list[dict[str, Any]], *, instant: bool = False) -> Any:
        payload: dict[str, Any] = {"list": entries}
        if instant:
            payload["instantActive"] = True
        return self.post("config.wan.connection.priority", payload)

    def apply_changes(self) -> Any:
        """保留中の設定変更を機器に反映する(Web GUI の Apply Changes 相当)。"""
        return self.post("cmd.config.apply")

    def discard_changes(self) -> Any:
        """保留中の設定変更を破棄する。"""
        return self.post("cmd.config.discard")
