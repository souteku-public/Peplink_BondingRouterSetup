"""InControl2 REST API クライアント(標準ライブラリのみ)。

認証: OAuth 2.0 client_credentials フロー
  POST https://api.ic.peplink.com/api/oauth2/token
  (client_id / client_secret は InControl2 のアカウント画面
   「Client Application」で発行する。README参照)

アクセストークンは約2日で失効するため、期限前に自動で再取得する。
レート制限: 組織あたり 20リクエスト/秒(超過時は HTTP 429)。

参考: https://www.peplink.com/ic2-api-doc/
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_API_BASE = "https://api.ic.peplink.com"


class IC2Error(Exception):
    """InControl2 API の呼び出し失敗。"""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)


class IC2Client:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = 30.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------ 認証

    def _grant_token(self) -> None:
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            }
        ).encode("ascii")
        req = urllib.request.Request(
            f"{self.api_base}/api/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise IC2Error(
                f"トークン取得に失敗しました (HTTP {exc.code}): {detail}\n"
                "client_id / client_secret と、Client Application が Enable になっているかを確認してください。",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise IC2Error(f"InControl2 に接続できません: {exc.reason}") from exc

        token = payload.get("access_token")
        if not token:
            raise IC2Error(f"トークン応答が不正です: {payload}")
        self._token = token
        # 失効の10分前には再取得する
        self._token_expiry = time.time() + float(payload.get("expires_in", 172799)) - 600

    def _ensure_token(self) -> str:
        if not self._token or time.time() >= self._token_expiry:
            self._grant_token()
        assert self._token is not None
        return self._token

    # ------------------------------------------------------------------ 通信

    def get(self, path: str, **params: Any) -> Any:
        """REST GET。data フィールド(あれば)を返す。

        401 はトークン再取得後に1回だけ再試行。429 は IC2Error(retry_after付き)にする。
        """
        for attempt in (1, 2):
            token = self._ensure_token()
            query = {k: v for k, v in params.items() if v is not None}
            query["access_token"] = token
            url = f"{self.api_base}{path}?" + urllib.parse.urlencode(query, doseq=True)
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    payload = json.loads(res.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code == 401 and attempt == 1:
                    self._token = None  # トークン失効 → 再取得して再試行
                    continue
                if exc.code == 429:
                    raise IC2Error(
                        "レート制限(20リクエスト/秒/組織)に達しました",
                        status=429,
                        retry_after=float(exc.headers.get("Retry-After") or 2),
                    ) from exc
                raise IC2Error(f"HTTP {exc.code}: {detail}", status=exc.code) from exc
            except urllib.error.URLError as exc:
                raise IC2Error(f"InControl2 に接続できません: {exc.reason}") from exc
        else:  # pragma: no cover
            raise IC2Error("認証リトライ上限")

        # 応答は {resp_code, data, ...} 形式のものと、素の配列のものがある
        if isinstance(payload, dict) and "resp_code" in payload:
            code = payload.get("resp_code")
            if code == "PENDING":
                # IC2が機器へ問い合わせ中。呼び出し側で少し待って再取得する
                raise IC2Error("PENDING", status=202, retry_after=2.0)
            if code != "SUCCESS":
                raise IC2Error(f"APIエラー: {code} {payload.get('message') or ''}".strip())
            return payload.get("data")
        return payload

    # ------------------------------------------------ よく使うエンドポイント

    def organizations(self) -> Any:
        return self.get("/rest/o")

    def groups(self, org_id: str) -> Any:
        return self.get(f"/rest/o/{org_id}/g")

    def devices(self, org_id: str, group_id: Any) -> Any:
        return self.get(f"/rest/o/{org_id}/g/{group_id}/d")

    def pepvpn_status(self, org_id: str, group_id: Any, device_id: Any) -> Any:
        """SpeedFusion のピア(プロファイル/対向)状態。"""
        return self.get(f"/rest/o/{org_id}/g/{group_id}/d/{device_id}/pepvpn/status")

    def tunnel_stat(self, org_id: str, group_id: Any, device_id: Any) -> Any:
        """SpeedFusionタブ相当: ピア別×WAN別の rx/tx・RTT・loss と機器側タイムスタンプ。"""
        return self.get(f"/rest/o/{org_id}/g/{group_id}/d/{device_id}/pepvpn/tunnel_stat")
