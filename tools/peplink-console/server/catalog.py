"""設定項目カタログ。

「どの設定項目が Router API で読めるのか / 書けるのか / まったく触れないのか」を
1か所に集約したテーブル。UIの「設定一覧」画面と監査エンジンの両方がこれを参照する。

用語:
  readable  … Router API で現在値を取得できる
  writable  … Router API で変更できる(cmd.config.apply で反映)
  manual    … Router API に該当エンドポイントが無く、Web GUI / InControl2 で操作するしかない

出典: Peplink Router API Documentation 8.5.0
  https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# 値の取り出しヘルパー
# ---------------------------------------------------------------------------


def _dig(obj: Any, *path: Any) -> Any:
    """ネストした dict / list を安全にたどる。存在しなければ None。"""
    cur = obj
    for key in path:
        if cur is None:
            return None
        if isinstance(key, int) and isinstance(cur, list):
            cur = cur[key] if 0 <= key < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _sim_entry(wan_cfg: dict, sim_id: int) -> dict | None:
    """cellular.sim 配列から SIM ID 一致のエントリを返す。"""
    for entry in _dig(wan_cfg, "cellular", "sim") or []:
        if isinstance(entry, dict) and entry.get("id") == sim_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# 項目定義
# ---------------------------------------------------------------------------


class Item:
    """設定項目1件のメタデータ。

    key       … ゴールデンコンフィグから参照する識別子(例 ``wan.2.healthcheck.method``)
    label     … 画面に出す日本語名
    category  … 画面上のグループ
    getter    … WAN設定オブジェクト(または snapshot)から現在値を取り出す関数
    write     … 変更用ペイロードを組む関数。None なら API で変更不可
    manual    … API非対応の理由(None なら API対応)
    ref       … 根拠となる同梱マニュアルの章
    options   … 選択肢(UIのプルダウン用)
    """

    __slots__ = ("key", "label", "category", "getter", "write", "manual", "ref", "options", "unit", "note")

    def __init__(
        self,
        key: str,
        label: str,
        category: str,
        *,
        getter: Callable[[Any], Any] | None = None,
        write: Callable[[Any], dict] | None = None,
        manual: str | None = None,
        ref: str | None = None,
        options: list[Any] | None = None,
        unit: str | None = None,
        note: str | None = None,
    ):
        self.key = key
        self.label = label
        self.category = category
        self.getter = getter
        self.write = write
        self.manual = manual
        self.ref = ref
        self.options = options
        self.unit = unit
        self.note = note

    @property
    def readable(self) -> bool:
        return self.getter is not None

    @property
    def writable(self) -> bool:
        return self.write is not None

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "readable": self.readable,
            "writable": self.writable,
            "manual": self.manual,
            "ref": self.ref,
            "options": self.options,
            "unit": self.unit,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# WAN共通の項目テンプレート
# ---------------------------------------------------------------------------

HEALTHCHECK_METHODS = ["ping", "nslookup", "http", "smartcheck"]


def _wan_common_items(wan_id: int, wan_label: str) -> list[Item]:
    p = f"wan.{wan_id}"
    n = f"{wan_label}"

    def wan_write(**body: Any) -> Callable[[Any], dict]:
        def _w(value: Any) -> dict:
            out: dict[str, Any] = {"id": wan_id}
            for k, builder in body.items():
                out[k] = builder(value) if callable(builder) else builder
            return out

        return _w

    return [
        Item(
            f"{p}.name",
            f"{n} / 接続名",
            "WAN共通",
            getter=lambda c: c.get("name"),
            write=lambda v: {"id": wan_id, "name": v},
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.enable",
            f"{n} / 有効",
            "WAN共通",
            getter=lambda c: c.get("enable"),
            write=lambda v: {"id": wan_id, "enable": bool(v)},
            options=[True, False],
            ref="02_ダッシュボード.md",
        ),
        Item(
            f"{p}.priority",
            f"{n} / 優先度",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "priority"),
            # 優先度は専用エンドポイント config.wan.connection.priority を使う
            write=lambda v: {"__priority__": True, "connId": wan_id, "priority": int(v)},
            note="1=最優先。0/未設定は無効(Disabled)",
            ref="02_ダッシュボード.md",
        ),
        Item(
            f"{p}.routing_mode",
            f"{n} / ルーティングモード",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "mode"),
            write=lambda v: {"id": wan_id, "connection": {"routingMode": v}},
            options=["NAT", "IP Forwarding"],
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.hot_standby",
            f"{n} / 待機時も接続維持(ホットスタンバイ)",
            "WAN共通",
            getter=lambda c: (
                _dig(c, "connection", "hotStandby", "enable")
                if isinstance(_dig(c, "connection", "hotStandby"), dict)
                else _dig(c, "connection", "hotStandBy")
            ),
            write=lambda v: {"id": wan_id, "connection": {"hotStandby": {"enable": bool(v)}}},
            options=[True, False],
            note="有効=フェイルオーバーが数秒。無効=接続確立から始まるため数十秒",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.icmp_ping",
            f"{n} / WAN側ICMP応答",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "icmpPing"),
            write=lambda v: {"id": wan_id, "connection": {"icmpPing": bool(v)}},
            options=[True, False],
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.dns_auto",
            f"{n} / DNS自動取得",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "dns", "auto"),
            write=lambda v: {"id": wan_id, "connection": {"dns": {"auto": bool(v)}}},
            options=[True, False],
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.bandwidth_upload",
            f"{n} / 申告帯域(上り)",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "bandwidth", "upload", "value"),
            write=lambda v: {
                "id": wan_id,
                "connection": {"bandwidth": {"upload": {"value": int(v), "unit": "kbps"}}},
            },
            unit="kbps",
            note="Weighted Balance と QoS の配分基準。実測の8割程度が目安",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.bandwidth_download",
            f"{n} / 申告帯域(下り)",
            "WAN共通",
            getter=lambda c: _dig(c, "connection", "bandwidth", "download", "value"),
            write=lambda v: {
                "id": wan_id,
                "connection": {"bandwidth": {"download": {"value": int(v), "unit": "kbps"}}},
            },
            unit="kbps",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.mtu",
            f"{n} / MTU",
            "WAN共通",
            getter=lambda c: _dig(c, "physical", "mtu"),
            write=lambda v: {"id": wan_id, "physical": {"mtu": int(v)}},
            note="未設定(None)は Auto。通常は変更しない",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.port_speed",
            f"{n} / ポート速度",
            "WAN共通",
            getter=lambda c: _dig(c, "physical", "speed"),
            write=lambda v: {"id": wan_id, "physical": {"speed": v}},
            note="有線WANのみ。通常は Auto",
            ref="03_WAN設定.md",
        ),
        # ------------------------------------------------ ヘルスチェック
        Item(
            f"{p}.healthcheck.enable",
            f"{n} / ヘルスチェック有効",
            "ヘルスチェック",
            getter=lambda c: _dig(c, "healthcheck", "enable"),
            write=lambda v: {"id": wan_id, "healthcheck": {"enable": bool(v)}},
            options=[True, False],
            note="無効にするとフェイルオーバーが機能しない",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.healthcheck.method",
            f"{n} / ヘルスチェック方式",
            "ヘルスチェック",
            getter=lambda c: _dig(c, "healthcheck", "method"),
            # POST時は method がオブジェクト {type, detail} になる(GETは文字列)
            write=lambda v: {
                "id": wan_id,
                "healthcheck": {"enable": True, "method": {"type": v, "detail": {}}},
            },
            options=HEALTHCHECK_METHODS,
            note="セルラーWANは smartcheck のみ(公式マニュアル)。有線/Wi-Fiは nslookup が既定",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.healthcheck.interval",
            f"{n} / チェック間隔",
            "ヘルスチェック",
            getter=lambda c: _dig(c, "healthcheck", "interval"),
            write=lambda v: {"id": wan_id, "healthcheck": {"enable": True, "interval": int(v)}},
            unit="秒",
            note="障害検知時間 ≒ 間隔 × リトライ回数",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.healthcheck.retry",
            f"{n} / 失敗と判定する回数",
            "ヘルスチェック",
            getter=lambda c: _dig(c, "healthcheck", "retry"),
            write=lambda v: {"id": wan_id, "healthcheck": {"enable": True, "retry": int(v)}},
            unit="回",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.healthcheck.recovery",
            f"{n} / 復帰と判定する回数",
            "ヘルスチェック",
            getter=lambda c: _dig(c, "healthcheck", "recovery"),
            write=lambda v: {"id": wan_id, "healthcheck": {"enable": True, "recovery": int(v)}},
            unit="回",
            note="小さすぎると不安定回線でフラッピングする",
            ref="03_WAN設定.md",
        ),
        # ------------------------------------------------ データ量監視
        Item(
            f"{p}.allowance.enable",
            f"{n} / データ量監視",
            "データ量監視",
            getter=lambda c: _dig(c, "bandwidthAllowanceMonitor", "enable"),
            write=lambda v: {"id": wan_id, "bandwidthAllowanceMonitor": {"enable": bool(v)}},
            options=[True, False],
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.allowance.limit",
            f"{n} / 月間上限",
            "データ量監視",
            getter=lambda c: _dig(c, "bandwidthAllowanceMonitor", "monthlyAllowance", "value"),
            write=lambda v: {
                "id": wan_id,
                "bandwidthAllowanceMonitor": {
                    "enable": True,
                    "monthlyAllowance": {"value": int(v), "unit": "MB"},
                },
            },
            unit="MB",
            ref="03_WAN設定.md",
        ),
        Item(
            f"{p}.allowance.action",
            f"{n} / 上限到達時の動作",
            "データ量監視",
            getter=lambda c: _dig(c, "bandwidthAllowanceMonitor", "action"),
            write=lambda v: {
                "id": wan_id,
                "bandwidthAllowanceMonitor": {
                    "enable": True,
                    "action": v if isinstance(v, list) else [v],
                },
            },
            options=[["email"], ["email", "disconnect"], []],
            note="disconnect を含めると上限到達でその回線が自動切断される",
            ref="03_WAN設定.md",
        ),
    ]


def _cellular_items(wan_id: int, wan_label: str, sim_ids=(1, 2)) -> list[Item]:
    """セルラーWAN固有の項目。SIMスロットごとに APN 等を持つ。"""
    p = f"wan.{wan_id}"
    n = wan_label
    items: list[Item] = [
        Item(
            f"{p}.cellular.sim_scheme",
            f"{n} / 使用するSIMスロット",
            "セルラー",
            getter=lambda c: _dig(c, "cellular", "simCardScheme"),
            write=lambda v: {"id": wan_id, "cellular": {"simCardScheme": v}},
            options=["", "1", "2", "alternate", "remote_sim"],
            note='""=両方(既定) / "1"=SIM Aのみ / "2"=SIM Bのみ / alternate=定期交互 / remote_sim=遠隔SIM',
            ref="04_セルラー設定.md",
        ),
        Item(
            f"{p}.cellular.preferred_sim",
            f"{n} / 優先SIM",
            "セルラー",
            getter=lambda c: _dig(c, "cellular", "preferredSim"),
            write=lambda v: {"id": wan_id, "cellular": {"preferredSim": int(v)}},
            options=[1, 2],
            ref="04_セルラー設定.md",
        ),
        Item(
            f"{p}.cellular.network_mode",
            f"{n} / ネットワークモード",
            "セルラー",
            getter=lambda c: _dig(c, "connection", "cellularModule", "networkMode")
            or _dig(c, "gobi", "mode"),
            write=lambda v: {"id": wan_id, "connection": {"cellularModule": {"networkMode": v}}},
            note="5G/LTE等の世代固定。安定性優先でLTE固定にする運用がある",
            ref="04_セルラー設定.md",
        ),
        Item(
            f"{p}.cellular.use_external_antenna",
            f"{n} / 外部アンテナを使用",
            "セルラー",
            getter=lambda c: _dig(c, "cellular", "useExternalAntenna"),
            write=lambda v: {"id": wan_id, "cellular": {"useExternalAntenna": bool(v)}},
            options=[True, False],
            ref="04_セルラー設定.md",
        ),
    ]

    for sim_id in sim_ids:
        slot = "A" if sim_id == 1 else "B"
        sp = f"{p}.sim{sim_id}"
        sn = f"{n} / SIM {slot}"

        def sim_write(sim=sim_id, **body):
            def _w(value: Any) -> dict:
                inner: dict[str, Any] = {"id": sim}
                for k, builder in body.items():
                    inner[k] = builder(value) if callable(builder) else builder
                return {"id": wan_id, "cellular": {"sim": [inner]}}

            return _w

        items += [
            Item(
                f"{sp}.apn",
                f"{sn} / APN",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "operator", "apn"),
                write=sim_write(operator=lambda v: {"apn": v}),
                note="スロットごとに設定が必要。未設定だと切替時に接続不能",
                ref="04_セルラー設定.md",
            ),
            Item(
                f"{sp}.apn_auto",
                f"{sn} / APN自動判定",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "operator", "auto"),
                write=sim_write(operator=lambda v: {"auto": bool(v)}),
                options=[True, False],
                ref="04_セルラー設定.md",
            ),
            Item(
                f"{sp}.apn_username",
                f"{sn} / APNユーザー名",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "operator", "username"),
                write=sim_write(operator=lambda v: {"username": v}),
                ref="04_セルラー設定.md",
            ),
            Item(
                f"{sp}.roaming",
                f"{sn} / データローミング",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "roaming", "enable"),
                write=sim_write(roaming=lambda v: {"enable": bool(v)}),
                options=[True, False],
                note="国内キャリアSIMでは無効のままにする",
                ref="04_セルラー設定.md",
            ),
            Item(
                f"{sp}.band_selection",
                f"{sn} / バンド固定",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "bandSelection"),
                write=sim_write(bandSelection=lambda v: v if v else None),
                note="None/空 = 自動(全バンド)。固定したまま移動すると圏外の原因",
                ref="04_セルラー設定.md",
            ),
            Item(
                f"{sp}.mobile_type",
                f"{sn} / 接続世代",
                "セルラー",
                getter=lambda c, s=sim_id: _dig(_sim_entry(c, s), "mobileType"),
                write=sim_write(mobileType=lambda v: v),
                options=["LTE", "3G", "2G"],
                ref="04_セルラー設定.md",
            ),
        ]
    return items


# ---------------------------------------------------------------------------
# Router API に存在しない設定項目(Web GUI / InControl2 でのみ操作可能)
# ---------------------------------------------------------------------------

MANUAL_ITEMS: list[Item] = [
    Item(
        "speedfusion.profile.config",
        "SpeedFusion プロファイル設定(対向/PSK/暗号化)",
        "SpeedFusion",
        manual="Router API に SpeedFusion プロファイルの設定エンドポイントが無い(status.pepvpn は状態の参照のみ)",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "speedfusion.wan_smoothing",
        "WAN Smoothing",
        "SpeedFusion",
        manual="Router API にボンディング品質パラメータのエンドポイントが無い",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "speedfusion.fec",
        "FEC(前方誤り訂正)",
        "SpeedFusion",
        manual="Router API にボンディング品質パラメータのエンドポイントが無い",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "speedfusion.traffic_distribution",
        "ボンディング方式(Bonding / Dynamic Weighted Bonding)",
        "SpeedFusion",
        manual="Router API に SpeedFusion プロファイルの設定エンドポイントが無い",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "speedfusion.wan_priority",
        "トンネルに参加させるWANと優先度",
        "SpeedFusion",
        manual="Router API に SpeedFusion プロファイルの設定エンドポイントが無い"
        "(ダッシュボードのWAN優先度とは別設定)",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "outbound_policy.rules",
        "アウトバウンドポリシー(回線の使い分けルール)",
        "詳細設定",
        manual="Router API にエンドポイントが無い",
        ref="07_詳細設定.md",
    ),
    Item(
        "firewall.rules",
        "ファイアウォール(アクセスルール)",
        "詳細設定",
        manual="Router API にエンドポイントが無い",
        ref="07_詳細設定.md",
    ),
    Item(
        "qos.settings",
        "QoS(帯域制御・アプリ優先度)",
        "詳細設定",
        manual="Router API にエンドポイントが無い",
        ref="07_詳細設定.md",
    ),
    Item(
        "port_forwarding.rules",
        "ポートフォワーディング / NAT マッピング",
        "詳細設定",
        manual="Router API にエンドポイントが無い",
        ref="07_詳細設定.md",
    ),
    Item(
        "lan.config",
        "LAN IP / DHCP / VLAN の設定変更",
        "LAN",
        manual="status.lan.profile は参照のみ(IP・マスク・VLAN ID)。設定変更用エンドポイントが無い",
        ref="06_LAN_ネットワーク設定.md",
    ),
    Item(
        "lan.static_route",
        "静的ルート",
        "LAN",
        manual="Router API にエンドポイントが無い",
        ref="06_LAN_ネットワーク設定.md",
    ),
    Item(
        "system.admin_security",
        "管理者設定(パスワード・WAN側公開・セッション時間)",
        "システム",
        manual="Router API にエンドポイントが無い。API有効化自体もWeb GUIで行う",
        ref="09_システム設定.md",
    ),
    Item(
        "system.notification",
        "メール通知 / SNMP / Syslog転送",
        "システム",
        manual="Router API にエンドポイントが無い",
        ref="09_システム設定.md",
    ),
    Item(
        "system.firmware_upgrade",
        "ファームウェア更新",
        "システム",
        manual="info.firmware は参照のみ。更新実行のエンドポイントが無い",
        ref="09_システム設定.md",
    ),
    Item(
        "system.config_backup",
        "設定バックアップの取得・復元",
        "システム",
        manual="cmd.config.restore は「工場出荷状態への初期化」であり、バックアップファイルの復元ではない",
        ref="09_システム設定.md",
    ),
    Item(
        "system.event_log",
        "イベントログの参照",
        "システム",
        manual="Router API にエンドポイントが無い",
        ref="11_トラブルシューティング.md",
    ),
    Item(
        "system.schedule",
        "スケジュール(時間帯によるON/OFF)",
        "システム",
        manual="WAN側でスケジュールIDの指定はできるが、スケジュール自体の定義はAPI非対応",
        ref="09_システム設定.md",
    ),
]

# 参照のみ可能(APIで読めるが書けない)項目
READONLY_ITEMS: list[Item] = [
    Item(
        "status.firmware",
        "ファームウェアバージョン",
        "参照のみ",
        getter=lambda s: _dig(s, "device", "firmware"),
        ref="09_システム設定.md",
    ),
    Item(
        "status.speedfusion.profiles",
        "SpeedFusion トンネルの確立状態",
        "参照のみ",
        getter=lambda s: _dig(s, "tunnels", "profiles"),
        note="status.pepvpn。状態は読めるが設定は変更できない",
        ref="05_SpeedFusion_VPN.md",
    ),
    Item(
        "status.lan.profiles",
        "LAN / VLAN の現在値(IP・マスク・VLAN ID)",
        "参照のみ",
        getter=lambda s: _dig(s, "lans"),
        ref="06_LAN_ネットワーク設定.md",
    ),
    Item(
        "status.clients",
        "接続クライアント一覧",
        "参照のみ",
        getter=lambda s: _dig(s, "client_count"),
        ref="11_トラブルシューティング.md",
    ),
    Item(
        "status.location",
        "GPS 位置情報",
        "参照のみ",
        getter=lambda s: _dig(s, "location"),
        ref="09_システム設定.md",
    ),
]


# ---------------------------------------------------------------------------
# カタログ生成
# ---------------------------------------------------------------------------


def build_catalog(snapshot: dict) -> list[Item]:
    """機器スナップショットから、その機器に実在するWANに応じた項目一覧を組み立てる。"""
    items: list[Item] = []
    for wan in snapshot.get("wans", []):
        wan_id = wan["id"]
        label = wan.get("name") or f"WAN {wan_id}"
        items += _wan_common_items(wan_id, label)
        if wan.get("type") in ("cellular", "gobi"):
            sim_ids = wan.get("sim_ids") or (1, 2)
            items += _cellular_items(wan_id, label, sim_ids)
    items += READONLY_ITEMS
    items += MANUAL_ITEMS
    return items


def catalog_index(snapshot: dict) -> dict[str, Item]:
    return {item.key: item for item in build_catalog(snapshot)}


def read_values(snapshot: dict) -> dict[str, Any]:
    """カタログの getter を使って、機器の現在値を key→value の平坦な辞書にする。"""
    values: dict[str, Any] = {}
    wan_configs = {w["id"]: (w.get("config") or {}) for w in snapshot.get("wans", [])}
    for item in build_catalog(snapshot):
        if item.getter is None:
            continue
        try:
            if item.key.startswith("wan."):
                wan_id = int(item.key.split(".")[1])
                values[item.key] = item.getter(wan_configs.get(wan_id, {}))
            else:
                values[item.key] = item.getter(snapshot)
        except Exception:  # 個別項目の失敗で全体を落とさない
            values[item.key] = None
    return values
