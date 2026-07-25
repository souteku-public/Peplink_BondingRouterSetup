# Peplink 設定管理コンソール

Peplink **MAX BR2 Pro** / **MAX Transit Pro Duo** の設定を、日本語のWeb画面から
確認・監査・変更するためのツールです。機器の公式 **Router API** を使って動作します。

できること:

| 画面 | 内容 |
|---|---|
| ① ダッシュボード | 全機器のWAN状態・電波品質・データ使用量・SpeedFusionトンネルを1画面に集約 |
| ② 設定監査 | 「あるべき値」(プロファイル)と機器の現在値を突合し、✓一致 / △要確認 / ✕不一致 / ⓘ手動確認で判定 |
| ③ 設定一覧 | この機器の全設定項目と現在値の一覧。**APIで読めるか・書けるか**を項目ごとに明示 |
| ④ 設定エディタ | 日本語ラベル+ヘルプ付きフォームで変更。差分を確認してから機器へ反映(Apply) |

> **重要**: Router API では触れない設定が多数あります(SpeedFusionのWAN Smoothing、
> アウトバウンドポリシー、ファイアウォールなど)。何が操作できて何ができないかは
> [「操作できない設定項目」](#操作できない設定項目) に全て記載しています。

---

## 目次

- [1. 動作要件](#1-動作要件)
- [2. インストール手順](#2-インストール手順)
- [3. 機器側の準備(APIを使えるようにする)](#3-機器側の準備apiを使えるようにする)
- [4. 接続設定(config.yaml)](#4-接続設定configyaml)
- [5. 起動と使い方](#5-起動と使い方)
- [6. 監査プロファイルの編集](#6-監査プロファイルの編集)
- [7. 操作できない設定項目](#操作できない設定項目)
- [8. 安全に使うための設計](#8-安全に使うための設計)
- [9. トラブルシューティング](#9-トラブルシューティング)
- [10. 構成ファイルの説明](#10-構成ファイルの説明)

---

## 1. 動作要件

| 項目 | 要件 |
|---|---|
| OS | Windows / macOS / Linux(いずれでも可) |
| Python | **3.9 以上**(3.11以上を推奨) |
| 追加パッケージ | **PyYAML のみ**(本体は標準ライブラリだけで動作します) |
| ブラウザ | Chrome / Edge / Safari / Firefox の最新版 |
| ネットワーク | ツールを動かすPCから、機器のWeb管理画面(既定 `https://192.168.50.1`)に到達できること |
| 機器のファームウェア | **8.1.1 以上**を推奨(設定の参照 `config.wan.connection` が8.1.1以降のため)。状態表示のみなら8.0.0以上 |

Pythonが入っているか確認:

```bash
python3 --version     # Windows の場合は python --version
```

入っていない場合は [python.org](https://www.python.org/downloads/) からインストールしてください
(Windowsではインストール時に「Add Python to PATH」にチェックを入れます)。

## 2. インストール手順

```bash
# 1) このリポジトリを取得
git clone <このリポジトリのURL>
cd Peplink_BondingRouterSetup/tools/peplink-console

# 2) 依存パッケージ(PyYAMLのみ)をインストール
python3 -m pip install -r requirements.txt

# 3) まず実機なしで画面を確認する(デモモード)
python3 run.py --demo
```

ブラウザで **http://127.0.0.1:8787/** を開くと、架空の2台分のデータで
全画面を操作できます。実機には一切接続しません。

> Windows では `python3` を `python` に読み替えてください。

## 3. 機器側の準備(APIを使えるようにする)

機器ごとに1回だけ、Web管理画面で以下を設定します。

### 3-1. APIアクセスを有効化

1. ブラウザで機器の管理画面を開く(例 `https://192.168.50.1`)
2. **System > Admin Security** を開く
3. **API アクセス**(表記は `Allow API Access` / `API Access` 等)を **有効** にする
4. ページ下部の **Save** → 画面右上の **Apply Changes** を押す

> ファームウェアによって項目名・配置が異なります。見つからない場合は
> System タブ配下を「API」で探してください。

### 3-2. 認証方法を決める(どちらか一方)

#### 方法A: APIトークン(推奨)

パスワードをファイルに書かずに済み、権限も限定できます。

1. 管理画面に **admin** でログイン(トークン発行には管理者権限が必要)
2. 同じPCのターミナルで、次のコマンドを実行してクライアントを作成します

```bash
# (1) 管理者としてログインし、Cookieを保存
curl -k -c cookies.txt -H "Content-Type: application/json" \
  -X POST -d '{"username":"admin","password":"機器の管理パスワード"}' \
  https://192.168.50.1/api/login

# (2) 参照専用クライアントを作成 → clientId と clientSecret が返る
curl -k -b cookies.txt -H "Content-Type: application/json" \
  -X POST -d '{"name":"Bonding Console (read-only)","scope":"api.read-only"}' \
  https://192.168.50.1/api/auth.client
```

返ってきた `clientId` / `clientSecret` を `config.yaml` に記入します。

- **参照だけで使う場合**: `"scope":"api.read-only"` ← まずはこちらを推奨
- **設定変更まで行う場合**: `"scope":"api"` を指定したクライアントを別途作成

> `-k` は自己署名証明書の検証を省略するオプションです。

#### 方法B: 管理者ユーザー名 / パスワード

`config.yaml` に `username` / `password` を書くだけで使えます。
**参照専用で運用したい場合**は、機器側で **Read-only ユーザー**を作成して
そのアカウントを指定してください(System > Admin Security)。

## 4. 接続設定(config.yaml)

```bash
cp config.example.yaml config.yaml
# エディタで config.yaml を編集
```

最小構成の例:

```yaml
allow_write: false          # まずは参照専用で始める(機器に書き込みません)

devices:
  - id: relay-01            # 内部識別子(任意)
    label: 中継車1号          # 画面に表示する名前
    model: MAX BR2 Pro      # 機種名(APIで取得できないため手入力)
    host: 192.168.50.1      # 機器のIPアドレス
    profile: live-broadcast # 監査に使うプロファイル
    client_id: xxxxxxxx     # 3-2 の方法Aで取得した値
    client_secret: yyyyyyyy

  - id: relay-02
    label: 中継車2号
    model: MAX Transit Pro Duo
    host: 192.168.51.1
    profile: live-broadcast
    username: admin         # 方法B(パスワード認証)の場合
    password: ********
```

指定できる項目:

| キー | 既定値 | 説明 |
|---|---|---|
| `id` | 自動採番 | 内部識別子。URLや保留変更の紐付けに使う |
| `label` | `host` の値 | 画面に表示する機器名 |
| `model` | なし | 機種名。**Router APIから取得できない**ため手入力 |
| `host` | (必須) | 機器のIPアドレスまたはFQDN |
| `scheme` | `https` | `https` を推奨。`http` は平文通信になる |
| `port` | なし | Web管理ポートを変更している場合に指定 |
| `profile` | なし | 監査プロファイル名(`profiles/*.yaml` の `profile:` の値) |
| `client_id` / `client_secret` | なし | APIトークン認証 |
| `username` / `password` | なし | 管理者アカウント認証 |
| `verify_tls` | `false` | 自己署名証明書のため既定で検証しない。社内CA証明書導入済みなら `true` |
| `timeout` | `15` | 機器へのHTTPタイムアウト(秒) |
| `use_proxy` | `false` | 既定で環境変数のプロキシ設定を無視する(LAN内機器へ直接接続するため) |

**`config.yaml` は `.gitignore` で除外済み**です(認証情報がGitに入りません)。

### 接続確認

```bash
python3 run.py --check
```

各機器へのログインと主要エンドポイントの疎通を確認して終了します。出力例:

```
--- 中継車1号 (192.168.50.1)
    ログイン成功  権限: GET=1 POST=0
    OK   info.firmware
    OK   status.wan.connection
    OK   config.wan.connection
    OK   status.pepvpn
```

`権限: POST=0` は参照専用の状態です(設定変更はできません)。

## 5. 起動と使い方

```bash
python3 run.py                       # 参照専用で起動(既定)
python3 run.py --allow-write         # 設定変更を許可して起動
python3 run.py --port 9000           # ポート変更(既定 8787)
python3 run.py --host 0.0.0.0        # 他のPCからもアクセスさせる(下記の注意を参照)
python3 run.py --demo                # 実機に接続しないデモモード
python3 run.py --check               # 接続確認のみ
python3 run.py -c 現場用.yaml         # 設定ファイルを指定
```

起動後、ブラウザで **http://127.0.0.1:8787/** を開きます。

> **`--host 0.0.0.0` の注意**: このツール自体に認証機構はありません。
> 他PCへ公開する場合は、信頼できるLAN内に限定するか、
> リバースプロキシで認証を挟んでください。既定の `127.0.0.1` は同じPCからのみアクセス可です。

### 画面の使い方

1. **① ダッシュボード** — 「今すぐ同期」で機器から最新状態を取得します。
   赤い表示は「対応が必要」を意味します。
2. **② 設定監査** — 機器とプロファイルを選ぶと突合結果が出ます。
   「問題のある項目のみ」で✕△だけに絞れます。
   `✕不一致` の行に **期待値へ修正** ボタンが出る場合、押すと④の保留リストに積まれます。
3. **③ 設定一覧** — この機器の全設定項目と現在値。「API対応」列で
   `読み書き可` / `参照のみ` / `API非対応(手動)` を絞り込めます。
   監査プロファイルに書く**キー名(`wan.2.healthcheck.method` 等)をここで確認**できます。
4. **④ 設定エディタ** — 値を変更すると右側の「保留中の変更」に差分が積まれます。
   **「機器に反映する(Apply)」を押すまで機器には一切送信されません。**

### 反映(Apply)の挙動

「機器に反映する」を押すと、次の順で実行されます。

1. `POST /api/config.wan.connection`(WAN設定の保存 — この時点では**保留**)
2. `POST /api/config.wan.connection.priority`(優先度変更がある場合)
3. `POST /api/cmd.config.apply`(**反映**。Web GUIの Apply Changes と同じ)

Web管理画面の「Save → Apply Changes」の2段階と同じ流れです。
項目によって該当WANの再接続(数十秒)が発生します。

## 6. 監査プロファイルの編集

`profiles/*.yaml` が「あるべき値」の定義です。同梱プロファイル:

| ファイル | 用途 |
|---|---|
| `live-broadcast.yaml` | ライブ中継・配信運用(瞬断防止・本番中の自動切断禁止を重視) |
| `backup-site.yaml` | 固定拠点バックアップ運用(確実なフェイルオーバーとデータ量管理を重視) |

ルールの書き方:

```yaml
rules:
  - key: wan.*.healthcheck.enable      # * でワイルドカード指定可
    expect: { equals: true }           # 判定条件(演算子は1つ)
    severity: error                    # error=✕ / warning=△
    when: { wan_type: [cellular] }     # 適用条件(省略可)
    reason: 無効だとフェイルオーバーが機能しません   # 画面に表示される説明
    ref: 03_WAN設定.md                  # 根拠マニュアル(リンクになる)
    fix_value: true                    # 「期待値へ修正」が書き込む値(省略可)
```

使える演算子:

| 演算子 | 意味 | 例 |
|---|---|---|
| `equals` / `not_equals` | 一致 / 不一致 | `{ equals: smartcheck }` |
| `in` / `not_in` | いずれかに含まれる / 含まれない | `{ in: [medium, high] }` |
| `is_set` | 設定済み(`true`)/ 未設定(`false`) | `{ is_set: true }` |
| `min` / `max` | 以上 / 以下 | `{ max: 10 }` |
| `between` | 範囲内 | `{ between: [2, 5] }` |
| `contains` / `not_contains` | 配列に含む / 含まない | `{ not_contains: disconnect }` |

その他のプロファイル設定:

- `skip_disabled_wan: true`(既定)— 無効化されたWANを監査対象外にします。
  未使用回線の設定不備を誤検知しないためです。個別ルールに `include_disabled: true`
  を書くと、そのルールだけ無効WANも対象にできます。
- `manual_checks:` — APIで読めない項目を「ⓘ 手動確認」として一覧に載せます。

## 操作できない設定項目

**このツールは Peplink 公式 Router API の範囲でのみ動作します。**
APIにエンドポイントが無い設定は、読むことも書くこともできません。
以下は Router API ドキュメント 8.5.0 に基づく一覧です(③設定一覧画面でも確認できます)。

### API非対応 — Web GUI / InControl2 でのみ操作可能

| 設定 | 備考 |
|---|---|
| **SpeedFusion プロファイル設定**(対向IP・Pre-shared Key・暗号化) | `status.pepvpn` で**状態の参照はできる**が、設定は不可 |
| **WAN Smoothing** | ボンディング品質パラメータのエンドポイントが存在しない |
| **FEC(前方誤り訂正)** | 同上 |
| **ボンディング方式**(Bonding / Dynamic Weighted Bonding) | 同上 |
| **トンネルに参加させるWANと優先度** | ダッシュボードのWAN優先度とは別設定。API非対応 |
| **アウトバウンドポリシー**(回線の使い分けルール) | エンドポイントが存在しない |
| **ファイアウォール**(アクセスルール) | 同上 |
| **QoS**(帯域制御・アプリ優先度) | 同上 |
| **ポートフォワーディング / NATマッピング** | 同上 |
| **LAN IP / DHCP / VLAN の変更** | `status.lan.profile` で IP・マスク・VLAN ID の**参照のみ**可能 |
| **静的ルート** | エンドポイントが存在しない |
| **管理者設定**(パスワード・WAN側公開・セッション時間) | API有効化自体もWeb GUIで行う必要がある |
| **メール通知 / SNMP / Syslog転送** | エンドポイントが存在しない |
| **ファームウェア更新** | `info.firmware` で**バージョン参照のみ**可能 |
| **設定バックアップの取得・復元** | `cmd.config.restore` は「工場出荷状態への初期化」であり、バックアップファイルの復元ではない |
| **イベントログの参照** | エンドポイントが存在しない |
| **スケジュール定義** | WAN側でスケジュールIDの指定はできるが、スケジュール自体の定義は不可 |
| **機種名・シリアル番号** | 取得するエンドポイントが無いため、`config.yaml` の `model` に手入力する |

### 参照のみ可能(読めるが変更できない)

- SpeedFusion トンネルの確立状態(プロファイル名・status・ピア情報)
- LAN / VLAN の現在値(IP・サブネットマスク・VLAN ID)
- ファームウェアバージョン
- 接続クライアント一覧
- GPS 位置情報
- WANのデータ使用量(実績値)

### 読み書きできる(このツールで変更可能)

WAN関連が中心です。1台あたり約80〜100項目が対象になります。

- WAN: 接続名、有効/無効、**優先度**、ルーティングモード(NAT / IP Forwarding)、
  待機時の接続維持(ホットスタンバイ)、WAN側ICMP応答、DNS自動取得、
  **申告帯域(上り/下り)**、MTU、ポート速度
- ヘルスチェック: 有効/無効、**方式**、チェック間隔、失敗判定回数、復帰判定回数
- データ量監視: 有効/無効、月間上限、**上限到達時の動作**(通知のみ / 自動切断)
- セルラー: **使用するSIMスロット**、優先SIM、ネットワークモード、外部アンテナ、
  SIMスロットごとの **APN** / APN自動判定 / ユーザー名 / **データローミング** /
  **バンド固定** / 接続世代

> 実際に何が読み書きできるかは機器のファームウェアにも依存します。
> **③ 設定一覧**画面が、その機器での実際の対応状況を示す正確な情報源です。

## 8. 安全に使うための設計

| 仕組み | 内容 |
|---|---|
| **既定で参照専用** | `allow_write: false` が既定。`--allow-write` か設定ファイルで明示しない限り、機器へ一切書き込みません |
| **2段階の反映** | 変更は保留リストに溜まり、「機器に反映する」を押すまで送信されません |
| **差分プレビュー** | 反映前に「現在値 → 変更後の値」と影響度(再接続の有無)を表示します |
| **認証情報をブラウザに渡さない** | 機器のパスワード/トークンはサーバー側のみで保持します |
| **権限の二重チェック** | 参照専用トークン/ユーザーの場合、サーバーが反映要求を拒否します |
| **書き込み対象の限定** | Router APIで書けない項目は、そもそも変更操作が出せません |

> **推奨する導入手順**: (1) `--demo` で画面を確認 → (2) 参照専用トークンで
> `--check` と参照運用 → (3) 監査結果を見て運用基準(プロファイル)を固める →
> (4) 必要なら書き込み権限を付与する。

### 変更前のバックアップについて

Router API には設定バックアップを取得するエンドポイントがありません。
**重要な変更の前には、Web管理画面の
System > Configuration > Download Active Configurations で
手動でバックアップを取得してください。**

## 9. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| `HTMLが返却されました` | APIアクセスが無効。[3-1](#3-1-apiアクセスを有効化) を実施してください。URL/ポートの誤りでも起きます |
| `192.168.50.1 に接続できません` | IPアドレス誤り、機器と別セグメントにいる、ファイアウォールで遮断されている |
| プロキシ環境で接続できない | 既定で環境変数のプロキシは無視します。逆にプロキシ経由が必要な場合は `use_proxy: true` を指定 |
| `HTTP 401` / `認証情報が...` | ユーザー名・パスワード、または clientId / clientSecret の誤り |
| `この認証情報には変更権限がありません` | 参照専用(`api.read-only`)のトークン、または Read-only ユーザーです。書き込み用の認証情報が必要です |
| `書き込みが無効です` | `allow_write: false`(既定)です。`--allow-write` を付けるか設定ファイルを変更してください |
| `config.wan.connection` だけ NG になる | ファームウェアが 8.1.1 未満です。状態表示は動きますが、設定の参照・変更はできません |
| 監査が「? 判定不能」ばかりになる | 現在値が取得できていません。`--check` で `config.wan.connection` の疎通を確認してください |
| ✕が出るが直したくない項目がある | その機器の運用に合っていないルールです。`profiles/*.yaml` から該当ルールを削除するか `severity: warning` に変更してください |
| PyYAML が無いと言われる | `python3 -m pip install -r requirements.txt` を実行してください |
| 反映後も値が変わらない | 機器側で `cmd.config.apply` が失敗している可能性があります。Web GUIで保留中の変更(赤いApply Changes)が残っていないか確認してください |

## 10. 構成ファイルの説明

```
tools/peplink-console/
├── README.md                このファイル
├── requirements.txt         依存パッケージ(PyYAMLのみ)
├── run.py                   起動スクリプト(CLI)
├── config.example.yaml      接続設定のテンプレート
├── .gitignore               config.yaml を除外
├── profiles/                監査プロファイル(あるべき値の定義)
│   ├── live-broadcast.yaml
│   └── backup-site.yaml
├── server/
│   ├── peplink.py           Router API クライアント(認証・GET/POST・Apply)
│   ├── collector.py         機器から状態・設定を吸い上げて正規化
│   ├── catalog.py           設定項目カタログ(読み書き可否の定義。ツールの中核)
│   ├── audit.py             監査エンジン(期待値との突合)
│   ├── demo.py              デモモード用の擬似機器
│   └── app.py               HTTPサーバー・内部API
└── web/                     画面(index.html / app.js / styles.css)
```

**設定項目を追加したいとき**は `server/catalog.py` にItemを1つ足します。
`getter`(参照方法)と `write`(書き込みペイロード)を書けば、
設定一覧・監査・エディタの3画面すべてに自動的に現れます。

---

## 参照資料

- [Peplink Router API Documentation 8.5.0](https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf) — 本ツールが準拠するAPI仕様
- [MAX Series User Manual (Firmware 8.3.0)](https://fcc.report/FCC-ID/U8G-P1MT03A/7273669.pdf) — 設定項目の意味
- 同梱の設定マニュアル: [`../../docs/`](../../docs/) — 各設定項目の解説(監査結果の「根拠」リンク先)

## 免責

本ツールは非公式のものであり、Peplink社の提供物ではありません。
設定変更は機器の通信に影響します。本番運用前にデモモードと参照専用モードで
挙動を確認し、重要な変更の前には設定バックアップを取得してください。
