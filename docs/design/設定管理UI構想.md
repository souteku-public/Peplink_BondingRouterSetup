# 設定管理UI 開発構想

> **本構想は実装済みです。** 動作するツールは [`tools/peplink-console/`](../../tools/peplink-console/)
> にあり、導入手順は [そのREADME](../../tools/peplink-console/README.md) を参照してください。
>
> 実装の過程でAPI仕様を精査した結果、**Router API では読み書きできない設定が
> 想定より多い**ことが判明しました(SpeedFusionのWAN Smoothing/FEC/ボンディング方式、
> アウトバウンドポリシー、ファイアウォール、QoS、LAN/VLANの変更など)。
> ツールはこれらを「ⓘ 手動確認」として明示し、Web GUIでの目視確認に誘導する設計にしています。
> 全一覧は [操作できない設定項目](../../tools/peplink-console/README.md#操作できない設定項目)。

## 1. 結論: 実現可能か

**可能です。** Peplink はファームウェア 8.0 以降、機器本体に **Router API**
(HTTPベースの公式ローカルAPI)を搭載しており、以下がプログラムから行えます。

- **状態の取得**(GET): WAN接続状態、電波指標、SpeedFusionトンネル状態、使用量など
- **設定の取得**(GET): 現在の設定値の読み出し(=「機器から値を吸い上げる」)
- **設定の変更**(POST): 設定書込みと Apply の実行

したがって、要望である
「視認性・操作感のよいUIから設定」「機器の現在値を吸い上げて正誤判定」は
**両方とも公式APIの範囲で実装できます**。ファームウェア改造や画面スクレイピングは不要です。

- 公式ドキュメント: [Peplink Router API Documentation (8.5.0)](https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf)
  (バージョン別PDFが download.peplink.com で公開)
- 補足: フリート全体をクラウド経由で扱う場合は **InControl2 REST API** という選択肢もある(後述)

## 2. Router API の概要(調査結果)

| 項目 | 内容 |
|---|---|
| エンドポイント形式 | `https://<機器IP>/api/<機能名>`。GET(参照)/ POST(変更・実行)、入出力はJSON |
| レスポンス | `{"stat": "ok", "response": {...}}` / `{"stat": "fail", ...}` |
| 認証方式1: 管理者ログイン | `/api/login` にユーザー名/パスワードをPOST → セッションCookie(`bauth`)で以降のAPIを呼ぶ |
| 認証方式2: APIトークン | client ID / client secret を発行しトークンで認証(ツール組込み向き。パスワードをツールに持たせない) |
| 権限レベル | Read-Only(状態・設定の参照のみ)/ Read-Write(設定変更可)/ Admin(クライアント・トークン管理) |
| 有効化 | Web GUI の System > Admin Security で API アクセスを有効化 |

### 重要な設計上の含意

- **Read-Onlyトークンだけで「吸い上げ+正誤判定」は完結**します。
  まずRead-Onlyで監査ツールとして作り始め、書込み(是正機能)は後から足せる —
  リスクの低い段階的開発が可能です。
- 設定変更もWeb GUIと同じ「保留 → Apply」のモデルであり、APIからApplyを打つまで反映されません。
  UI側で「差分プレビュー → 承認 → Apply」のワークフローを自然に組めます。

## 3. システム構成案

```
[ブラウザ]
   │ HTTPS
[管理UI Webアプリ]  ← 視認性の高い日本語UI(ダッシュボード/設定フォーム/監査結果)
   │
[バックエンド]      ← Router APIクライアント、正誤判定エンジン、認証情報管理
   │ HTTPS (Router API)          │ HTTPS (Router API)
[MAX BR2 Pro]              [MAX Transit Pro Duo]
```

- **バックエンドを挟む理由**: (1) 機器の認証情報をブラウザに置かない
  (2) ブラウザから機器への直接アクセスはCORS/自己署名証明書の問題がある
  (3) 複数機器の一括取得・履歴保存・判定ロジックをサーバー側に集約できる
- 技術スタックの想定(確定は実装時): バックエンド = Python(FastAPI)または Node.js、
  フロントエンド = React。機器台数が少ないため小規模構成で十分。

## 4. 中核機能: 「あるべき値」との突合(正誤判定)

### 仕組み

1. **ゴールデンコンフィグ(あるべき値)** を機器/用途プロファイルごとにYAML等で定義
2. バックエンドが Router API で **現在値を吸い上げ**
3. 判定エンジンが両者を突合し、**項目ごとに ✅一致 / ⚠️要確認 / ❌不一致** を判定
4. UIに機器×項目のマトリクスで表示。不一致には「期待値・現在値・影響・該当マニュアル章」を併記

### ゴールデンコンフィグのイメージ

```yaml
profile: live-broadcast          # 中継運用プロファイル
applies_to: [MAX-BR2-PRO, MAX-TST-PRO-DUO]
rules:
  - key: wan.cellular1.health_check.method
    expect: not_equals: disabled          # ヘルスチェック無効は禁止
    severity: error
    manual: docs/03_WAN設定.md
  - key: wan.cellular1.standby_state
    expect: equals: remain_connected      # ホットスタンバイ必須
    severity: error
  - key: speedfusion.profile[main].wan_smoothing
    expect: in: [medium, high]            # 配信時の推奨レンジ
    severity: warning
  - key: system.admin.password_default
    expect: equals: false                 # 初期パスワード禁止
    severity: error
```

- 単純な「値の一致」だけでなく、**範囲・禁止値・項目間の整合**
  (例: 「SIM Both運用なら両スロットのAPNが設定済みであること」)もルール化できます。
- マニュアル([docs/](../))の各章とルールを紐付けることで、
  「なぜこの値であるべきか」をUIから直接参照できるようにします。

### 画面イメージ(主要3画面)

> 実際に操作できるモックアップを [UIモックアップ.html](UIモックアップ.html) として同梱しています
> (ブラウザで開くと3画面をタブで切り替えられます。データはすべて架空のサンプルです)。

1. **フリートダッシュボード**: 機器ごとの適合率・WAN状態・電波指標を一覧。異常は赤表示
2. **監査詳細**: 機器×設定項目の突合結果。不一致行に「期待値へ修正」ボタン(Read-Write段階で有効化)
3. **設定エディタ**: マニュアルと同じ機能領域別のタブ構成で、Web GUIより少ないページ数・
   日本語ラベル・入力バリデーション付きのフォーム。変更は差分プレビュー → Apply の2段階

## 5. 段階的な開発ロードマップ(案)

| フェーズ | 内容 | リスク |
|---|---|---|
| 1. 読み取り専用モニタ | Read-Onlyトークンで状態・設定を吸い上げ、ダッシュボード表示 | 低(機器に一切書き込まない) |
| 2. 正誤判定 | ゴールデンコンフィグとの突合・監査レポート | 低 |
| 3. 設定変更(限定) | 影響の小さい項目(通知設定、優先度など)から書込み対応。差分プレビュー必須 | 中 |
| 4. フル設定管理 | 主要設定のフォーム化、プロファイル一括適用、変更履歴 | 中〜高 |

## 6. 検討事項・制約

| 論点 | 内容 |
|---|---|
| API有効化 | 各機器で System > Admin Security からAPIアクセスを有効化する初期設定が必要 |
| 到達性 | ツールから機器のWeb管理ポートに到達できる必要がある。遠隔地の機器はVPN(SpeedFusion/社内網)経由か、InControl2 API の利用を検討 |
| InControl2 APIとの使い分け | IC2はクラウド経由でフリート一括管理ができるが、設定粒度・リアルタイム性はRouter APIが上。**少数台数・詳細制御なら Router API、多拠点大規模なら IC2 併用**が目安 |
| IC2との競合 | 機器がIC2管理下にある場合、ローカル書込みがIC2に上書きされる項目がある。書込み対象はどちらで管理するか方針を先に決める |
| ファームウェア差 | APIのフィールドはFWバージョンで増減する。判定エンジンは「未知キーは無視+警告」の設計にする |
| 認証情報の管理 | 機器パスワードは使わずAPIトークンを使用。トークンはバックエンドの秘匿ストアに保管 |
| 監査ログ | 誰がいつ何を変更したかをツール側で記録(Web GUI直接変更と区別できる) |

## 7. まずやること(実装開始時の最初の一歩)

1. 検証機で System > Admin Security の API アクセスを有効化
2. `curl` で `/api/login` → 状態取得(例: WAN状態)を試し、自環境のFWバージョンで
   取得できるJSONの実物を確認する
3. その実物JSONを元に、ゴールデンコンフィグの「key」体系を確定する

---
参考資料:
- [Peplink Router API Documentation 8.5.0 (PDF)](https://download.peplink.com/resources/Peplink-Router-API-Documentation-8.5.0.pdf)
- [Peplink Router API Documentation for Firmware 8.0.0 (PDF)](https://download.peplink.com/resources/Peplink-Router-API-Documentation-for-Firmware-8.0.0.pdf)
- [Peplink KnowHow: Router API Documentation](https://knowhow.peplink.ninja/peplink-router-api-documentation/)
