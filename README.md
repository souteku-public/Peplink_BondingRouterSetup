# Peplink ボンディングルーター 設定マニュアル & 設定管理UI構想

Peplink 製ボンディングルーター **MAX BR2 Pro** および **MAX Transit Pro Duo (MAX-TST-PRO-DUO)** の
Web管理画面(Web Admin GUI)の設定項目を、「何をいじると何が起きるのか」という観点で整理した運用マニュアルです。

Web GUI はページ数・項目数が多く、英語表記であるため、本マニュアルでは
**機能領域ごとに分冊**し、各設定項目について以下を統一フォーマットで記載しています。

- **項目名**(GUI上の英語表記)
- **意味** — その設定が何を制御しているか
- **変更すると起きること** — 実際の通信・動作への影響
- **注意点** — 変更時のリスク、推奨値、再起動や切断の有無

## 対象機器・ファームウェア

| 機器 | 型番 | 主な構成 |
|---|---|---|
| MAX BR2 Pro | MAX-BR2-PRO 系 | デュアル内蔵セルラーモデム / Wi-Fi 6 / GPS / SpeedFusion 対応 |
| MAX Transit Pro Duo | MAX-TST-PRO-DUO | デュアル内蔵LTEモデム(CAT-7 + CAT-12、各モデム2 SIMスロット) / Wi-Fi 6 / GPS / SpeedFusion 対応 |

- 記載はファームウェア **8.x 系(8.3〜8.5 世代)** の Web 管理画面を基準にしています。
- 両機種とも同じファームウェア体系・同じ管理画面構成のため、マニュアルは共通です。
  機種差(モデム数、ポート数など)がある箇所は本文中に明記しています。
- ファームウェアのバージョンによりメニュー名・配置が多少異なることがあります。

## 目次

| # | ドキュメント | 内容 |
|---|---|---|
| 01 | [機器概要とログイン・基本操作](docs/01_機器概要とログイン.md) | ログイン方法、Save と Apply の2段階適用、設定バックアップ |
| 02 | [ダッシュボード](docs/02_ダッシュボード.md) | WAN優先度の操作、接続状態の見方 |
| 03 | [WAN設定(共通)](docs/03_WAN設定.md) | 優先度、ヘルスチェック、帯域監視、DNS、MTU |
| 04 | [セルラー設定](docs/04_セルラー設定.md) | SIMスロット、APN、バンド固定、ローミング、電波状態の読み方 |
| 05 | [SpeedFusion / VPN(ボンディング)](docs/05_SpeedFusion_VPN.md) | ボンディング、WAN Smoothing、FEC、サブトンネル |
| 06 | [LAN・ネットワーク設定](docs/06_LAN_ネットワーク設定.md) | LAN IP、DHCP、VLAN、ポート設定 |
| 07 | [詳細設定(Advanced)](docs/07_詳細設定.md) | アウトバウンドポリシー、ポートフォワーディング、ファイアウォール、QoS |
| 08 | [AP(Wi-Fi)設定](docs/08_AP_WiFi設定.md) | SSID、セキュリティ、チャンネル、Wi-Fi WANとの共存 |
| 09 | [システム設定](docs/09_システム設定.md) | 管理者設定、ファームウェア更新、時刻、通知、InControl2 |
| 10 | [運用レシピ集](docs/10_運用レシピ集.md) | 中継・ライブ配信向けボンディング設定、フェイルオーバー構成などの実例 |
| 11 | [トラブルシューティング](docs/11_トラブルシューティング.md) | つながらない・切れる・遅い時の切り分け手順 |

## 設定管理コンソール(実装済みツール)

公式 Router API を使い、**日本語のWeb画面から設定を確認・監査・変更する**ツールを同梱しています。

```bash
cd tools/peplink-console
python3 -m pip install -r requirements.txt
python3 run.py --demo          # 実機なしで画面を確認できます
```

- **導入手順・接続設定・操作方法**: [tools/peplink-console/README.md](tools/peplink-console/README.md)
- 画面: ①ダッシュボード(全機器の状態集約) ②設定監査(あるべき値との突合)
  ③設定一覧(全項目と現在値・API対応状況) ④設定エディタ(差分確認 → 反映)
- 設計構想とAPI仕様の調査結果: [docs/design/設定管理UI構想.md](docs/design/設定管理UI構想.md)
- 画面イメージ(静的モックアップ): [docs/design/UIモックアップ.html](docs/design/UIモックアップ.html)

> **Router API で操作できない設定があります**(SpeedFusionのWAN Smoothing、
> アウトバウンドポリシー、ファイアウォールなど)。全一覧は
> [ツールのREADME「操作できない設定項目」](tools/peplink-console/README.md#操作できない設定項目) を参照してください。

## 参照資料

本マニュアルは以下の公式資料を参照し、記載内容(項目名・選択肢・公称値)を照合しています。

| 資料 | 用途 |
|---|---|
| [MAX Series User Manual (BR2, Firmware 8.3.0)](https://fcc.report/FCC-ID/U8G-P1MT03A/7273669.pdf) | Web GUI の設定項目・選択肢・既定値・公称値の照合(WAN Smoothing 倍率、FEC オーバーヘッド、ヘルスチェック方式、SpeedFusion 使用ポート等) |
| [Peplink Router API Documentation 8.5.0](https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf) | 設定管理UI構想における Router API 仕様の確認 |
| [Firmware 8.5.x リリースノート](https://download.peplink.com/resources/firmware-8.5.3-release-notes.pdf) | 対応機種・機能差分の確認 |

## 免責

本マニュアルはメーカー公式ドキュメントおよびファームウェア 8.x 系の一般的な画面構成に基づく参考資料です。
公式ユーザーマニュアルはファームウェア 8.3.0 世代を照合基準としており、8.4 以降では項目の追加・変更があり得ます。
実運用での変更前には必ず設定バックアップを取得し、お使いのファームウェアに対応する公式マニュアル・リリースノートも併せて確認してください。
