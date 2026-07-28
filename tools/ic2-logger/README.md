# InControl2 SpeedFusion ロガー

クラウド管理サービス **InControl2** から、機器詳細ページの **SpeedFusionタブに
表示される回線ごとの情報**(WAN別スループット・遅延(RTT)・ドロップ)を取得し、
**データが更新されたタイミングだけ** CSV に記録し続けるツールです。

- InControl2 公式 REST API(`pepvpn/tunnel_stat`)を使用します
- ポーリングは数秒間隔で行いますが、応答に含まれる機器側タイムスタンプを見て
  **前回から更新されている時だけ**記録します(同じデータで行が増えません)
- [設定管理コンソール](../peplink-console/)とは**独立した別アプリケーション**です
  (併用も、これ単体での利用も可能)

---

## 目次

- [1. 動作要件](#1-動作要件)
- [2. インストール](#2-インストール)
- [3. InControl2 側の準備(認証情報の発行)](#3-incontrol2-側の準備認証情報の発行)
- [4. 設定(config.yaml)](#4-設定configyaml)
- [5. 使い方](#5-使い方)
- [6. 出力ファイルの形式](#6-出力ファイルの形式)
- [7. データの更新間隔について](#7-データの更新間隔について)
- [8. 常時稼働させる(サービス化)](#8-常時稼働させるサービス化)
- [9. トラブルシューティング](#9-トラブルシューティング)

---

## 1. 動作要件

| 項目 | 要件 |
|---|---|
| OS | Windows / macOS / Linux |
| Python | 3.9 以上 |
| 追加パッケージ | PyYAML のみ(本体は標準ライブラリで動作) |
| ネットワーク | `https://api.ic.peplink.com` に接続できること(社内プロキシ経由の場合は環境変数 `HTTPS_PROXY` を設定) |
| InControl2 | 対象機器が InControl2 の組織/グループに登録され、オンラインであること |

## 2. インストール

```bash
cd Peplink_BondingRouterSetup/tools/ic2-logger
python3 -m pip install -r requirements.txt

# まず接続なしで動作を確認(合成データで約30秒動かしてCtrl+C)
python3 run.py --demo
# → logs/ にCSVができていれば環境はOKです
```

> Windows では `python3` を `python` に読み替えてください。

## 3. InControl2 側の準備(認証情報の発行)

APIを使うための Client ID / Client Secret を発行します(1回だけの作業)。

1. ブラウザで [InControl2](https://incontrol2.peplink.com/) にサインイン
2. 画面**右上の自分のメールアドレス(ユーザー名)**をクリックし、アカウント情報ページを開く
3. ページ**最下部の「Client Application」**セクションで **New Client** をクリック
4. アプリ名(例: `SpeedFusion Logger`)を入力し、**Enable にチェック**して保存
5. 表示された **Client ID / Client Secret** を控える(config.yaml に記入します)

> この方法で発行したIDは「自分のアカウントがアクセスできる組織・機器」に対して
> 有効です。本ツールは読み取り専用のエンドポイントしか呼びません。

## 4. 設定(config.yaml)

```bash
cp config.example.yaml config.yaml
```

まず認証情報だけ記入して、対象のIDを調べます:

```bash
python3 run.py --list
```

```
組織: 〇〇株式会社  (organization_id: abcDEF)
  グループ: 中継車  (group_id: 12)
    機器: 中継車1号  (device_id: 34567, S/N: 1927-XXXX, 状態: online, 製品: MAX BR2 Pro)
    機器: 中継車2号  (device_id: 34568, S/N: 2833-XXXX, 状態: online, 製品: MAX Transit Pro Duo)
```

表示された ID を config.yaml に転記します:

```yaml
client_id: xxxxxxxxxxxxxxxx
client_secret: yyyyyyyyyyyyyyyy
organization_id: abcDEF
group_id: 12
devices:
  - id: 34567
    label: 中継車1号
  - id: 34568
    label: 中継車2号
interval: 5          # ポーリング間隔(秒)
output_dir: logs
formats: [csv]       # csv / jsonl(生データ)
```

`config.yaml` は `.gitignore` 済みで、Gitにはコミットされません。

## 5. 使い方

```bash
python3 run.py --once        # 1回だけ取得して中身を表示(疎通確認)
python3 run.py               # 記録開始(Ctrl+C で停止)
python3 run.py --interval 3  # ポーリング間隔を一時的に変更
python3 run.py -c 現場A.yaml  # 設定ファイルを指定
```

実行中の表示例:

```
SpeedFusion ロガー開始 — InControl2 に接続
機器数: 2 / ポーリング間隔: 5.0秒 / 出力: logs (csv)
機器側の統計が更新されたタイミングのみ記録します。停止は Ctrl+C。
[14:00:12] 中継車1号: 2行 記録
[14:00:22] 中継車1号: 2行 記録
```

## 6. 出力ファイルの形式

出力先: `logs/<label>_<日付>.csv`(機器×日付ごとに1ファイル、日付が変わると自動で切替)

| 列 | 内容 |
|---|---|
| `logged_at` | ロガーが記録した時刻(ローカルタイム) |
| `device_ts` | **機器側で統計が採取された時刻**(UNIX秒)。グラフ化はこちらを使うのが正確 |
| `sn` | 機器のシリアル番号 |
| `peer_id` | SpeedFusion ピア(対向)のID |
| `conn_id` / `wan_name` | WAN接続のIDと名前(Cellular 1 など) |
| `state` | トンネル内でのそのWANの状態(ACTIVE など) |
| `rtt_ms` | 遅延(ミリ秒) |
| `loss` | ドロップ数の累計 |
| `rx_bytes` / `tx_bytes` | 受信/送信バイトの累計カウンタ |
| `rx_bps` / `tx_bps` | **受信/送信スループット(bit/秒)** — 前回記録との差分から計算 |
| `loss_delta` | 前回記録からのドロップ増分 |

- 1回の更新につき「ピア×WAN」の組み合わせごとに1行ずつ書かれます
  (例: トンネル1本・セルラー2回線なら、1回の更新で2行)
- 最初の1行は差分の元がないため `rx_bps` / `tx_bps` は空になります
- `formats: [csv, jsonl]` にすると、APIの生データ(JSONL)も並行保存します

## 7. データの更新間隔について

- InControl2 が機器から受け取る統計はおおむね **10秒前後の粒度**です
  (SpeedFusionタブのグラフと同じデータソース)。本ツールはそれより短い間隔で
  ポーリングし、**更新を検知した時だけ**記録するため、取りこぼしなく・重複なく
  「InControl2が持つ最小粒度」で記録できます。
- 初回アクセス時などにAPIが `PENDING`(InControl2が機器へ問い合わせ中)を返す
  ことがありますが、自動で数秒待って再取得します。
- APIのレート制限は**組織あたり20リクエスト/秒**です。機器2台を5秒間隔で
  ポーリングする場合は毎秒0.4リクエストであり、余裕があります。
- それ以上細かい粒度(秒単位)が必要な場合は、InControl2 経由ではなく機器から
  直接取得する必要があります(SNMP経由。[調査メモ](../../docs/design/高頻度スループット記録_調査.md)参照)。

## 8. 常時稼働させる(サービス化)

### Linux (systemd)

`/etc/systemd/system/ic2-logger.service`:

```ini
[Unit]
Description=InControl2 SpeedFusion Logger
After=network-online.target

[Service]
WorkingDirectory=/opt/Peplink_BondingRouterSetup/tools/ic2-logger
ExecStart=/usr/bin/python3 run.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ic2-logger
journalctl -u ic2-logger -f     # ログ確認
```

### Windows(タスクスケジューラ)

「タスクスケジューラ」→ 基本タスクの作成 → トリガー「スタートアップ時」→
操作「プログラムの開始」で以下を指定:

- プログラム: `python`
- 引数: `run.py`
- 開始(作業フォルダ): `C:\...\tools\ic2-logger`

## 9. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| `トークン取得に失敗しました (HTTP 401)` | client_id / client_secret の誤り、または Client Application が Enable になっていない |
| `--list` で組織が表示されない | そのアカウントが組織に参加していない。InControl2 のWeb画面で見えている組織か確認 |
| `PENDING のまま応答が確定しません` | 機器がオフライン、またはInControl2との接続が不安定。InControl2 の機器一覧で状態を確認 |
| 記録される行が想定より少ない | 正常な場合が多い(**更新があった時だけ**記録する仕様)。トンネルが確立していない機器は更新が発生しません |
| `rate_limit_exceeded` (429) | 機器台数が多い場合は `interval` を大きくする(自動でバックオフもします) |
| プロキシ環境で接続できない | 環境変数 `HTTPS_PROXY` を設定(本ツールはOS標準のプロキシ設定に従います) |
| CSVをExcelで開くと文字化けする | UTF-8のため、Excelは「データ > テキストから」でUTF-8を指定して読み込む |

## 使用しているAPI(参考)

| エンドポイント | 用途 |
|---|---|
| `POST /api/oauth2/token` | 認証(client_credentials)。トークンは約2日有効で自動更新 |
| `GET /rest/o` / `/rest/o/{org}/g` / `.../g/{gid}/d` | `--list` でのID探索 |
| `GET /rest/o/{org}/g/{gid}/d/{dev}/pepvpn/tunnel_stat` | **本体**。SpeedFusionタブ相当のピア別×WAN別統計 |

出典: [InControl 2 API Documentation](https://www.peplink.com/ic2-api-doc/)

## 免責

本ツールは非公式のものであり、Peplink社の提供物ではありません。
読み取り専用APIのみを使用し、機器・InControl2の設定は一切変更しません。
