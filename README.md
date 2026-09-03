# Shuttlers Match App 🏸

バドミントンイベントの参加者管理、コート数に応じたダブルス組み合わせ生成、試合結果・履歴管理を行う Flask ベースの Web アプリケーションです。

## 🔍 概要

v1.6.1 では、従来の参加者管理、組み合わせ生成、試合履歴、LINE通知機能に加えて、管理者向けにPayPayリンクの有効期限警告、履歴ダンプのSMTPメール送信、環境ごとのLINE Messaging有効・無効切り替えに対応しています。

主な機能は次のとおりです。

- 参加者登録（名前、性別、競技レベル、カード）
- 参加者の有効 / 無効状態と試合回数の管理
- コート数に応じたダブルス組み合わせ生成
- 試合回数を考慮した組み合わせ生成
- 未確定の仮組み合わせ編集
- `/match/edit` で現在のペアを固定し、固定ペアを崩さずに手動調整できます
- `/match/edit` の「スコアが近いペアで組み直す」ボタンから、bench を変えずに出場者のペアと対戦を再調整できます
- ペア作成時はランダム性を残しつつ、現役 DB 上の `MatchHistory` で過去に組んだことのあるペアをできる限り避けます
- できあがったペア同士は、ペアスコアが近い組み合わせで対戦するように並べます
- 連続出場ベンチ優先回数の設定変更
- 組み合わせ確定と試合結果表示
- 試合履歴管理
- 勝敗・スコア入力
- 履歴の JSON ダンプ
- 試合履歴JSONダンプのSMTPメール送信
- JSON保存・メール送信失敗時も履歴削除・全データ削除を継続するベストエフォートバックアップ
- ダンプ済み履歴の参照
- 仮組み合わせ編集画面でのプレイヤースコア・ペアスコア表示
- DB 上の入力済み試合履歴から算出した勝率によるプレイヤースコア補正
- 参加費案内と QR コード支払い
- 管理者トップでのPayPayリンク有効期限警告
- LINE Bot による通知登録
- 参加者ごとの LINE アカウント連携
- 組み合わせセッション単位の通知登録
- 組み合わせ確定時の LINE Push 通知
- 参加者ごとに、自分のコート番号と同じコートのメンバーを通知
- ベンチ参加者への待機通知
- LINE 連携成功時の PayPay 支払い案内
- LINE 通知登録導線を優先した参加登録完了画面
- `LINE_MESSAGING_ENABLED` による環境ごとのLINE機能切り替え
- 管理者ビューでの設定や状態操作

## 🛠 使用技術

- Python 3.10+
- Flask
- Flask-SQLAlchemy
- SQLite
- Bootstrap (Flaskテンプレート内)
- JavaScript (一部動的UI)
- JSON（設定ファイル・状態管理・履歴ダンプ）
- pytest（自動テスト）

## 🚀 セットアップ方法

```bash
git clone https://github.com/nowdon/shuttlers-match-app.git
cd shuttlers-match-app
python -m venv venv
source venv/bin/activate  # Windows の場合は venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp config.example.json config.json
# ローカル開発では次のいずれかを設定してください（例）
export SECRET_KEY="local-dev-secret"
# または、ローカル開発専用の固定 fallback を明示的に許可します
export ALLOW_DEV_SECRET_KEY=1

# 本番環境では推測困難な値を設定してください（例）
export SECRET_KEY='replace-with-a-long-random-secret'
```

`config.json` はローカル環境ごとの設定ファイルです。Git 管理対象外のため、初回セットアップ時は `config.example.json` をコピーして必要に応じて編集してください。主な設定項目は次のとおりです。

```json
{
  "paypay_links": {
    "adults": "https://example.com/pay/adults",
    "students": "https://example.com/pay/students"
  },
  "paypay_link_expirations": {
    "adults": "2026-07-25",
    "students": "2026-07-25"
  },
  "history_dump_email": {
    "enabled": false,
    "recipient": ""
  },
  "score_input_mode": "winner_only",
  "consecutive_play_limit": 3
}
```

- `paypay_links`: 社会人用・学生用の PayPay 支払いリンクです。
- `paypay_link_expirations`: PayPay 支払いリンクの有効期限です。`YYYY-MM-DD` 形式で指定します。URL が設定されている場合、期限の前日以降に管理者トップで警告します。未設定でも起動できます。
- `score_input_mode`: 勝敗・スコア入力方式です。`winner_only` または `score` を指定します。
- `history_dump_email`: 履歴ダンプをSMTPメールで送信するかと送信先を指定します。SMTP接続情報やパスワードは環境変数から取得します。
- `consecutive_play_limit`: 何回連続出場したら次回ベンチ優先対象にするかを指定します。未設定時は `3` として扱います。設定範囲は `2` 〜 `10` で、`/admin/settings` から変更できます。

実際の `config.example.json` には、支払いリンク、レベル設定、性別ごとの weight、スコア設定なども含まれます。実際の PayPay リンクや環境固有の値は `config.json` にだけ保存してください。

`SECRET_KEY` は Flask の session cookie 署名に使います。`SECRET_KEY` が設定されている場合はその値を使用します。未設定の場合、デフォルトでは起動に失敗します。ローカル開発だけで固定 fallback を使いたい場合は、明示的に `ALLOW_DEV_SECRET_KEY=1` を設定してください。本番環境では必ず環境変数 `SECRET_KEY` に推測困難な値を設定し、`ALLOW_DEV_SECRET_KEY=1` は使わないでください。

LINE Bot Webhook で通知登録を受け付ける場合は、本番環境だけで `LINE_MESSAGING_ENABLED` を有効化し、LINE Developers で発行した次の環境変数を設定してください。

```bash
export LINE_MESSAGING_ENABLED="1"
export LINE_CHANNEL_SECRET="..."
export LINE_CHANNEL_ACCESS_TOKEN="..."
export LINE_BOT_FRIEND_URL="https://lin.ee/xxxxxxx"
```

- `LINE_MESSAGING_ENABLED` は `1`、`true`、`on`、`yes` の場合のみ LINE Messaging 機能を有効にします。本番環境だけで有効化してください。
- `LINE_MESSAGING_ENABLED` が未設定、または上記以外の値の場合、LINE 機能は完全に無効になります。thanks ページの LINE 通知登録 UI、通知登録開始、Webhook 処理、組み合わせ確定時の LINE Push 通知、通知管理レコード作成はいずれも実行されません。
- 本番環境では `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、必要に応じて `LINE_BOT_FRIEND_URL` を設定してください。
- `LINE_CHANNEL_SECRET` は Webhook 署名検証に使います。
- `LINE_CHANNEL_ACCESS_TOKEN` は reply / push message 送信に使います。
- `LINE_BOT_FRIEND_URL` は LINE 連携コード画面の「LINEでBotを開く」ボタンに使います。
- `LINE_BOT_FRIEND_URL` が未設定でもアプリは起動し、画面表示も壊れないようにしています。
- 開発環境では LINE 関連環境変数を設定しなくても、組み合わせ確定は通常どおり実行できます。
- 本番では HTTPS の `/line/webhook` を LINE Developers の Webhook URL に設定してください。

## 🧭 状態管理と Flask session の方針

このアプリでは、業務状態の正本を client 単位の Flask session ではなく、用途ごとの共有 state store に分けて管理します。

- Flask session に保存してよい値は、`flash()` が使う一時通知の `_flashes` のみです。
- `match_state.json`: 確定済み組み合わせ、待機者、試合回数、試合の有効状態などの実行時状態を保存します。
- `draft_state.json`: 未確定の仮組み合わせ、待機者、現在編集中の draft だけで有効な固定ペア (`fixed_pairs`) を保存します。仮組み合わせの作成・編集で `match_state.json` を上書きしないための状態です。
- SQLite DB (`instance/participants.db`): 参加者、試合履歴、ベンチ履歴、LINE 通知関連情報などを保存します。
  - 参加者の氏名、カード、性別、レベル、weight、`active`、`games_played` は DB を正とします。
  - 組み合わせ確定時の履歴は `MatchRound`、`MatchHistory`、`BenchHistory` として DB に保存されます。
  - v1.6.0 では、現在の組み合わせセッションを表す `MatchSession`、参加者と LINE userId の紐付けを表す `LineAccount`、セッション単位の通知登録を表す `NotificationSubscription`、LINE 連携コードを表す `LineLinkToken`、参加者ごとの通知送信ログを表す `NotificationDeliveryLog`、`session_id + match_count + channel` 単位の送信済み管理を表す `MatchNotification` も DB で管理します。
- `instance/history_dumps/`: JSON ダンプされた過去履歴を保存します。Git 管理対象外です。
- `draft_matches`、`draft_bench`、`court_count`、`last_confirmed_*` を Flask session に再導入しないでください。

`SECRET_KEY` は Flask の session cookie 署名に使うため、環境変数 `SECRET_KEY` から設定します。本番環境では `SECRET_KEY` の設定を必須とし、推測困難な値を指定してください。ローカル開発でのみ、明示的に `ALLOW_DEV_SECRET_KEY=1` を設定した場合に固定 fallback の利用を許可します。本番環境では `ALLOW_DEV_SECRET_KEY=1` を使わないでください。


### `draft_state.json` の例

```json
{
  "draft": true,
  "matches": [[1, 2, 3, 4]],
  "bench": [5],
  "court_count": 1,
  "fixed_pairs": [[1, 2]]
}
```

- `matches`: 未確定の仮組み合わせを participant id で保持します。
- `bench`: 現在の draft の待機者を participant id で保持します。
- `court_count`: draft 作成時または編集中のコート数です。
- `fixed_pairs`: 固定中のペアを participant id のペアとして保持します。
- `[1, 2]` と `[2, 1]` は同じ固定ペアとして扱います。
- `fixed_pairs` は現在の draft だけに有効で、confirm 後や次回生成時には引き継ぎません。
- `fixed_pairs` は DB や履歴には保存しません。


## ▶️ 起動方法

初回起動前、または参加者DBを作り直したい場合は SQLite のテーブルを作成します。

```bash
python init_db.py
```

開発サーバーを起動します。

```bash
python app.py
```

http://localhost:5001 でアクセスできるようになります。
(ポートの変更はapp.pyの最終行で指定してください。)

## 🌐 主なURL

| URL | 用途 |
| --- | --- |
| `/` | 参加者向けビューへリダイレクト |
| `/viewer` | 参加者向けカード一覧・試合状況 |
| `/register` | 参加者登録 |
| `/notifications/line/start/<card>` | LINE 通知登録開始 |
| `/line/webhook` | LINE Bot Webhook。LINE Developers から呼ばれる URL であり、通常ユーザーが直接開く画面ではありません。 |
| `/participant/<card>` | 参加者情報の編集 |
| `/match` | 組み合わせ生成フォーム |
| `/match/edit` | 未確定の仮組み合わせ編集、ペア固定、固定ペアを維持した swap、スコアが近いペアでの再調整 |
| `/match/result` | 確定済み組み合わせ表示（admin モードでは結果入力も可能） |
| `/admin` | 管理者トップ |
| `/admin/settings` | 管理者設定 |
| `/admin/match_history` | 現役試合履歴、勝敗・スコア入力、履歴ダンプ |
| `/admin/match_history_archives` | ダンプ済み履歴一覧 |
| `/admin/match_history_archives/<filename>` | ダンプ済み履歴詳細 |

## 🔐 モードについて

本アプリには一般参加者向けの viewer モードと管理者向けの admin モードがあります。現時点では管理者ログインは不要という仕様です。管理者 endpoint を public internet に安全に公開できることを意味するものではないため、運用環境ではアクセス制御やネットワーク制限を別途検討してください。

### viewerモード

http://localhost:5001/viewer でアクセスします。`/` からも viewer モードへリダイレクトされます。

- 参加者の登録が可能
- 参加者情報の修正が可能
- 確定済み組み合わせと試合状況の閲覧が可能
- 保存済みの勝敗・スコア結果の閲覧や入力は admin モードのみ対応です

### adminモード

http://localhost:5001/admin でアクセスします。

管理者向け機能は次のとおりです。

- 参加者管理（登録、編集、有効 / 無効状態の管理、CSV 取り込みなど）
- コート数設定
- 組み合わせ生成
- 仮組み合わせ編集
- ペア固定と固定ペアを維持した手動 swap
- スコアが近いペアで組み直す再調整
- 組み合わせ確定
- 確定済み組み合わせと試合結果の表示
- 試合結果入力
- 試合履歴表示
- 履歴ダンプ
- 履歴ダンプ後の DB 履歴消去
- ダンプ済み履歴表示
- 全データ削除時の履歴自動ダンプ
- スコア入力モード設定
- 連続出場ベンチ優先回数の設定
- ブラウザ上での `config.json` 修正（`/admin/settings`）
- 参加者 DB の内容を全削除

## ✏️ 仮組み合わせ編集とペア固定（v1.5.0）

`/match/edit` では、生成後・確定前の draft を管理者が調整できます。v1.5.0 では、特定のペアを固定したまま他の参加者を入れ替えるペア固定モードに対応しています。

- 同じペアの 2 人を選択して swap 操作すると、そのペアを固定できます。
- 固定済みペアの 2 人を再度選択して swap 操作すると、固定を解除できます。
- 固定ペアの片方を他の出場者と swap すると、固定ペアはペア単位で移動します。
- 固定ペアの片方と bench 参加者の個別 swap はできません。固定ペアを崩さずに調整するための制限です。
- 固定ペアは画面上の「固定」バッジなどで確認できます。
- 固定ペアは現在編集中の 1 回分の draft 内だけで有効です。試合確定後や次回の組み合わせ生成には引き継がれません。

### 「スコアが近いペアで組み直す」ボタン

admin モードの `/match/edit` には、「スコアが近いペアで組み直す」ボタンがあります。この処理は通常の組み合わせ生成時には自動実行されず、管理者が任意に押したときだけ実行されます。

- admin モードでのみ表示・実行できます。viewer モードでは利用できません。
- 現在の `fixed_pairs` を維持します。
- bench は変更しません。
- `fixed_pairs` 以外の出場者をランダムにペア化します。
- 現役 DB 上の `MatchHistory` を対象に、過去に組んだことのあるペアの重複が少なくなるように複数回試行します。
- ダンプ済み JSON の履歴は参照しないため、履歴ダンプ後に DB 上の履歴を消去すると、その期間のペア履歴は回避判定に使われません。
- ペア作成時には `player_score` / `pair_score` による均等化は行わず、`fixed_pairs` 以外の出場者をランダムにペア化します。
- スコアは、完成したペア同士を `pair_score` が近い対戦になるように並べる段階でのみ使用します。

## 🔔 LINE通知機能（v1.6.0）

v1.6.0 では、参加者登録後の LINE 通知登録、組み合わせ確定時の個人別 LINE Push 通知、LINE 連携成功時の PayPay 支払い案内に対応しています。`LINE_MESSAGING_ENABLED` が未設定の場合は LINE 機能全体が無効になり、開発環境では LINE 関連環境変数なしで組み合わせ確定できます。

- 参加登録完了後の thanks ページから LINE 通知登録を開始できます。
- thanks ページでは LINE 通知登録を主導線として、PayPay 支払い案内より上に表示します。
- LINE 連携成功後、LINE 上で PayPay 支払いリンクと参加費を案内します。
- LINE を使わない参加者向けに、Web 上にも PayPay 支払いリンクを残しています。
- 未連携参加者には `LineLinkToken` による連携コードを発行します。
- 連携コード画面では、コードをコピーし、LINE Bot を開いて、コードを貼り付けて送信する導線にしています。
- Webhook で連携コードを受け取り、`LineAccount` と `NotificationSubscription` を作成します。
- 通知登録は `MatchSession` 単位で管理します。
- 前回参加者や過去セッションの通知登録者には、今回セッションの通知は送りません。
- 組み合わせ確定時、現在セッションで通知登録済みの参加者だけに LINE 通知を送ります。
- 同一 `session_id + match_count + channel` の `MatchNotification` により、同じ回の二重送信を防止します。
- 同じセッションでも `match_count` が変われば次の回として通知されます。
- 参加者が試合に入っている場合は、自分のコート番号と同じコートの 4 人を通知します。
- ベンチの場合は待機メッセージを通知します。
- LINE 通知には `/match/result` の絶対 URL を含めます。
- LINE 連携成功時には PayPay 支払いリンクと参加費を案内します。

試合参加者向け通知例:

```text
第1回目

あなたは 1コートです

♥A 田中・♠3 鈴木
vs
♣5 佐藤・♣2 山田

結果はこちら
https://example.com/match/result
```

ベンチ通知例:

```text
第1回目

今回は待機です。
次の組み合わせまでお待ちください。

結果はこちら
https://example.com/match/result
```

## 📋 試合履歴機能（v1.4.0）

v1.4.0 では、組み合わせ確定後の履歴管理が強化されています。

- 組み合わせ確定時に、ラウンド単位の `MatchRound`、各コートの試合を表す `MatchHistory`、待機者を表す `BenchHistory` として DB に保存されます。
- `/admin/match_history` で、現在 DB に残っている現役履歴を確認できます。
- 現役履歴では勝敗・スコア入力ができます。
- ラウンド単位で複数コートの結果を一括保存できます。
- admin モードの `/match/result` からも、最新の確定済み組み合わせに対して結果入力できます。
- viewer モードの `/match/result` では確定済み組み合わせと試合状況のみ閲覧でき、保存済みの勝敗・スコア結果の表示や入力 UI は admin モードのみ対応です。


## ⚙️ 管理者設定

`/admin/settings` では、ローカル設定ファイル `config.json` の一部をブラウザ上で変更できます。

- PayPay リンクと有効期限 (`paypay_links`, `paypay_link_expirations`)
- スコア入力モード (`score_input_mode`)
- score モード用のスコア設定
- 連続出場ベンチ優先回数 (`consecutive_play_limit`)

`consecutive_play_limit` は、何回連続で出場した参加者を次回できる限りベンチ側に回すかを決める設定です。デフォルトは `3` です。設定範囲は `2` 〜 `10` で、未設定または範囲外の値はデフォルトの `3` として扱います。

## 📝 勝敗・スコア入力

スコア入力方式は `/admin/settings` のスコア入力モードで設定します。

### `winner_only` モード

- 勝敗だけを入力するモードです。
- 各試合で `team1 勝利` / `team2 勝利` / `未入力` を保存します。
- `winner_team` が `1` または `2` の試合だけが勝率集計対象です。

### `score` モード

- ゲームごとのスコアを入力するモードです。
- 設定項目は次のとおりです。
  - `points_per_game`: 1 ゲームの基準ポイント
  - `games_per_match`: 1 試合のゲーム数
  - `deuce_enabled`: デュースを有効にするか
  - `max_points`: デュース時などを含めた最大ポイント
- score モードでは、入力されたゲームカウントから勝敗を自動判定します。
- 未入力や引き分けは勝率集計対象外です。

## 📈 勝率によるプレイヤースコア補正

仮組み合わせ編集画面などで参考情報として表示するプレイヤースコアは、競技レベル・補正値・勝率を使って計算されます。

```text
player_score = level_score * weight + win_rate
```

- `level_score` は競技レベルに応じた基本スコアです。
- `weight` は性別などに応じた補正値です。
- `win_rate` は DB 上の入力済み試合履歴から算出される勝率です。
- `winner_team` が入力済み（`1` または `2`）の `MatchHistory` のみ勝率集計対象です。
- 未入力・引き分けは勝率集計対象外です。
- 履歴がない参加者の `win_rate` は `0.0` です。
- 履歴を JSON にダンプして DB 上の履歴を消去すると、勝率補正は `0.0` に戻ります。

## 🗃 履歴ダンプとダンプ済み履歴

### 履歴ダンプ

- `/admin/match_history` から、現在 DB に残っている試合履歴を JSON にダンプできます。
- 履歴を JSON にダンプしてから、DB 上の履歴を消去できます。
- 全データ削除時には、削除前に履歴が自動で JSON ダンプされます。
- ダンプ JSON は `instance/history_dumps/` 配下に保存されます。
- `instance/history_dumps/` は Git 管理対象外です。
- ダンプ JSON には、ラウンド、試合、ベンチ、参加者名、カード、スコア、勝敗などが含まれます。


### SMTPメール送信

`/admin/settings` では、試合履歴JSONダンプをローカル保存後にメール添付で送信するかを設定できます。送信方式はAmazon SES APIやOSの `sendmail` / `mail` / Postfix には依存しない標準SMTPです。同じPythonコードをAmazon EC2上のUbuntu、一般的なUbuntu、macOSで利用できます。

`config.json` には有効/無効と送信先だけを保存します。SMTPホスト、ユーザー名、パスワードなどの接続情報は環境変数から読み込み、パスワードを `config.json` へ保存しません。Gmail、Amazon SES SMTP、社内SMTPリレーなど、任意のSMTPサービスへ環境変数の切り替えだけで接続先を変更できます。

必要な環境変数は次のとおりです。

| 環境変数 | 内容 |
| --- | --- |
| `SMTP_HOST` | SMTPサーバーのホスト名（必須） |
| `SMTP_PORT` | SMTPポート。未設定時はSSLが465、それ以外は587 |
| `SMTP_SECURITY` | `starttls`、`ssl`、`none` のいずれか |
| `SMTP_USERNAME` | SMTPユーザー名。空なら認証しません |
| `SMTP_PASSWORD` | SMTPパスワード |
| `SMTP_FROM_EMAIL` | Fromメールアドレス（必須） |
| `SMTP_FROM_NAME` | From表示名 |
| `SMTP_TIMEOUT_SECONDS` | SMTP接続タイムアウト秒数。未設定時は約10秒 |

STARTTLS（通常587番）の例:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_SECURITY=starttls
export SMTP_USERNAME=your-smtp-user
export SMTP_PASSWORD=your-smtp-password
export SMTP_FROM_EMAIL=no-reply@example.com
export SMTP_FROM_NAME="Shuttlers Match App"
```

SSL/TLS（通常465番）の例:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=465
export SMTP_SECURITY=ssl
export SMTP_USERNAME=your-smtp-user
export SMTP_PASSWORD=your-smtp-password
export SMTP_FROM_EMAIL=no-reply@example.com
```

認証なしローカルSMTPリレーの例:

```bash
export SMTP_HOST=localhost
export SMTP_PORT=25
export SMTP_SECURITY=none
export SMTP_FROM_EMAIL=no-reply@example.com
unset SMTP_USERNAME
unset SMTP_PASSWORD
```

JSON保存とメール送信はベストエフォートのバックアップ処理です。手動の「履歴をJSONダンプ」はJSON保存失敗時のみ失敗として扱い、JSON保存後のメール送信に失敗した場合は保存済みJSONを残して警告します。「履歴を削除してダンプ」と「全データ削除」では、JSON保存失敗やメール送信失敗が履歴消去・全データ削除を中止することはありません。JSON保存またはメール送信に失敗した場合は管理画面のflashメッセージとログで警告し、削除処理そのものが失敗した場合だけ削除失敗として扱います。

### ダンプ済み履歴表示

- `/admin/match_history_archives` でダンプ済み履歴一覧を確認できます。
- 一覧にはダンプ日時、reason、ラウンド数、試合数、ベンチ数などが表示されます。
- 詳細ページでラウンド単位の過去履歴を確認できます。
- ダンプ済み履歴は参照専用です。
- ダンプ済み履歴からの復元や編集は未対応です。

## ⚠️ 運用上の注意

- 履歴を消去する前に JSON ダンプされます。
- ただし、ダンプ済み JSON は現在の勝率集計対象ではありません。
- 勝率集計は現在 DB に残っている `MatchHistory` のみが対象です。
- 長期運用で履歴を期間ごとに区切りたい場合は、履歴をダンプして DB 上の履歴を消去してください。
- 全データ削除時にも履歴は自動ダンプされます。
- `instance/history_dumps/` は Git 管理対象外のため、必要に応じてサーバー側でバックアップしてください。
- ペア固定は 1 回の編集中 draft だけに有効で、試合確定後や次回生成時には引き継がれません。
- 「スコアが近いペアで組み直す」は、bench を変更しません。
- スコア調整は通常の組み合わせ生成時には自動実行されません。
- ペア作成時にはスコアで均等化せず、`fixed_pairs` 以外をランダムにペア化します。
- スコアは、完成したペア同士の対戦調整にのみ使用します。
- 過去ペア回避は現役 DB 上の `MatchHistory` のみを対象にし、ダンプ済み JSON の履歴は参照しません。
- 古い `draft_state.json` との互換はありますが、不正な `fixed_pairs` などは安全のため再生成を促す場合があります。
- `config.json`、`match_state.json`、`draft_state.json`、`*.db`、`instance/history_dumps/` はローカルまたは実行時データです。実データや secret、決済リンクを Git に commit しないでください。

## 🧪 テスト

pytest 構成を用意しています。ロジック、状態管理、履歴管理、スコア計算、参加者情報を変更したら、PR 作成前に以下を実行してください。

```bash
pytest -q
```

テスト設定は `pytest.ini` に集約しており、`tests/` 配下の `test_*.py` を対象にしています。v1.5.0 では、ペア固定、固定ペア swap、スコアが近いペアで組み直す処理、legacy draft 互換、malformed `fixed_pairs` 防御、admin-only POST 制御などもテスト対象です。v1.6.0 では、LINE 連携コード、Webhook 署名検証、通知登録、Push 通知、個人別通知文、二重送信防止、DeliveryLog / MatchNotification などもテスト対象です。

## 🗂 ディレクトリ構成（例）

```
shuttlers-match-app/
├── app.py
├── models.py
├── logic.py
├── routes/
│   └── api.py
├── instance/
│   ├── participants.db          # SQLite DB（Git管理対象外）
│   └── history_dumps/           # 履歴ダンプJSON（Git管理対象外）
├── templates/
│   ├── admin_settings.html
│   ├── index.html
│   ├── match_edit.html
│   ├── match_form.html
│   ├── match_history.html
│   ├── match_history_archives.html
│   ├── match_result.html
│   ├── participant_edit.html
│   ├── register.html
│   ├── thanks.html
│   └── upload_csv.html
├── static/
│   ├── participants_template.csv
│   └── cards/
│       └── ※カード画像を配置（詳細は下記）
├── tests/
│   ├── test_logic.py
│   ├── test_match_history.py
│   ├── test_score.py
│   └── ...
├── utils/
│   ├── config.py
│   ├── db_utils.py
│   ├── draft_state.py
│   ├── line_push.py
│   ├── mail_sender.py
│   ├── match_session.py
│   ├── match_state.py
│   ├── pair_optimizer.py
│   ├── reset.py
│   ├── score.py
│   ├── stats.py
│   └── ...
├── manual-site/        # 操作マニュアル用の独立したWebサイト
├── config.example.json
├── config.json          # ローカル設定（Git管理対象外）
├── match_state.json     # 実行時状態（Git管理対象外）
├── draft_state.json     # 実行時状態（Git管理対象外）
├── pytest.ini
├── requirements.txt
├── CHANGELOG.md
└── README.md
```

## 🃏 カード画像について

本アプリではトランプの絵柄画像を使用しています。以下の画像を **`static/cards` フォルダ** に各自で用意・配置してください。

### 🎴 ファイル名ルール

- ♣ クラブ: `cA.png`, `c2.png`, ..., `cJ.png`, `cQ.png`, `cK.png`
- ♠ スペード: `sA.png`, `s2.png`, ..., `sJ.png`, `sQ.png`, `sK.png`
- ♦ ダイヤ: `dA.png`, `d2.png`, ..., `dJ.png`, `dQ.png`, `dK.png`
- ♥ ハート: `hA.png`, `h2.png`, ..., `hJ.png`, `hQ.png`, `hK.png`
- 🃏 ジョーカー: `joker_black.png`, `joker_red.png`

※ 画像サイズは統一されていることが望ましいです。

## 📄 ライセンス

MIT License

## 👤 作者

[@nowdon](https://github.com/nowdon)
