# AGENTS.md

## Project Overview

Shuttlers Match App は、バドミントンイベントの参加者管理、ダブルスの
組み合わせ生成、試合履歴管理、参加者通知を行う Python 3.10+ / Flask
アプリケーションです。

現在の主な構成は次のとおりです。

- `app.py`: Flask アプリケーションのエントリポイント、画面ルート、主要処理
- `models.py`: Flask-SQLAlchemy の各種データモデル
- `logic.py`: ダブルスの組み合わせ生成ロジック
- `routes/api.py`: 参加者情報と試合状態を返す JSON API
- `utils/`: 状態管理、スコア計算、DB 操作、リセット等の共通処理
- `templates/`: Jinja テンプレート
- `static/`: カード画像、CSV テンプレート等の静的ファイル
- `tests/`: pytest による自動テスト
- `manual-site/`: 参加者・管理者向けマニュアルサイト

`models.py` では、参加者情報だけでなく、試合履歴、組み合わせセッション、
LINE 通知関連情報も管理しています。

主なモデルは次のとおりです。

- `Participant`
- `MatchRound`
- `MatchHistory`
- `BenchHistory`
- `MatchSession`
- `LineAccount`
- `NotificationSubscription`
- `LineLinkToken`
- `MatchNotification`
- `NotificationDeliveryLog`

参加者向け画面と管理者向け画面は URL で分離されています。

- Participant view: `/viewer`（`/` からリダイレクト）
- Administrator view: `/admin`
- Administrator settings: `/admin/settings`

現時点では管理者ログインは不要という仕様です。

明示的な仕様変更の依頼がない限り、認証機能を追加したり、参加者向け画面と
管理者向け画面を統合したりしないでください。


## Branch Policy

- 開発の基準ブランチは `develop` です。
- 作業開始前に現在のブランチと `git status` を確認してください。
- 通常の機能追加や修正は、最新の `develop` から作業ブランチを作成します。
- `main` に直接 commit または push しないでください。
- `develop` にも、ユーザーから明示的な指示がない限り直接 commit しないでください。
- Pull Request の通常の対象ブランチは `develop` です。
- リポジトリ管理者から明示的な指示がない限り、`main` 向けの変更を作成しないで
  ください。
- commit は依頼された作業単位に絞り、無関係なリファクタリング、整形、
  生成データ、ローカル環境の変更を混在させないでください。
- ユーザーによる未 commit の変更を破棄、上書き、巻き戻ししないでください。


## Branch Naming Rules

作業ブランチを作成する場合は、内容が分かる名前にしてください。

禁止例:

```text
codex
codex-abc123
codex-random-string
```

推奨形式:

```text
codex/<task-summary>
```

例:

```text
codex/add-match-history-archives
codex/fix-reset-dump-failure
codex/update-readme-v1-6-1
codex/prioritize-bench-fairness
codex/add-round-score-save
codex/refactor-pre-cloudflare-cleanup
```

ルール:

- `codex/` prefix を付ける
- 作業内容が分かる英語の kebab-case にする
- ランダム文字列だけの名前にしない
- `codex` 単体のブランチ名を使わない
- Git ref 衝突を避けるため、`codex` と `codex/...` を混在させない
- 既存の `codex/...` ブランチ体系がある場合は、必ず
  `codex/<task-summary>` を使う


## Codex CLI Workflow

Codex CLI を使ったローカル開発では、リポジトリ全体を調査し、必要に応じて
複数ファイルを横断して修正して構いません。

ただし、変更範囲は依頼されたタスクに必要な範囲に限定してください。

### 作業開始時

必ず以下を確認してください。

```bash
git branch --show-current
git status
git fetch origin
```

必要に応じて `develop` と現在の作業ブランチとの差分も確認してください。

ユーザーの未 commit の変更がある場合は内容を確認し、今回の作業と無関係な
変更をそのまま保持してください。

未 commit の変更を削除、reset、checkout、restore、上書きしてはいけません。

### 通常の作業フロー

基本的な作業順序は次のとおりです。

1. 現在のコードと関連テストを調査する
2. 必要に応じて作業前のテストを実行する
3. 必要最小限の範囲を修正する
4. 関連テストを実行する
5. 全体への影響がある場合は全テストを実行する
6. `git diff --check` を実行する
7. `git status` と `git diff` を確認する
8. 結果をユーザーへ報告する

### Git 操作

ユーザーが明示的に指示していない限り、以下は行わないでください。

- `git commit`
- `git push`
- Pull Request の作成
- branch の merge
- tag の作成
- release の作成
- `main` または `develop` への直接 commit

修正とテストが完了した時点で一度停止し、ユーザーの確認を受けてください。

作業完了時は最低限、次を報告してください。

- 調査結果
- 変更内容
- 変更したファイル
- 実行したテスト
- テスト結果
- `git diff --check` の結果
- 残っている課題
- 今回あえて変更しなかった事項
- commit 前に確認すべき事項


## Setup Commands

リポジトリ直下で仮想環境を作成し、依存パッケージとローカル設定を準備します。

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Windows では次のコマンドで仮想環境を有効化します。

```powershell
venv\Scripts\activate
```

初回起動前に SQLite のテーブルを作成します。

```bash
python init_db.py
```

`config.example.json` は公開可能な設定例です。

実際の PayPay リンク、メール送信先、環境固有の値、secret 等は Git 管理対象外の
設定または環境変数で管理してください。


## Environment Variables

本番環境では少なくとも、使用する機能に応じて以下の環境変数を扱います。

### Flask

```text
SECRET_KEY
ALLOW_DEV_SECRET_KEY
```

- `SECRET_KEY` は Flask session cookie の署名に使用します。
- 本番環境では `SECRET_KEY` を必須とします。
- `ALLOW_DEV_SECRET_KEY=1` はローカル開発でのみ使用できます。

### LINE Messaging

```text
LINE_MESSAGING_ENABLED
LINE_CHANNEL_SECRET
LINE_CHANNEL_ACCESS_TOKEN
LINE_BOT_FRIEND_URL
```

- LINE Messaging を使用する本番環境でのみ有効化してください。
- `LINE_MESSAGING_ENABLED` が無効な環境では、LINE 関連機能を勝手に有効化して
  はいけません。
- token や channel secret を source code や設定例へ書き込んではいけません。

### History Dump Email

履歴ダンプメール送信で使用する SMTP 接続情報、認証情報等は環境変数から
取得します。

SMTP password 等の credential を `config.json`、source code、test fixture、
log に保存しないでください。


## Test Commands

リポジトリ直下で全テストを実行します。

```bash
pytest
```

pytest の設定は `pytest.ini` にあります。

個別に実行する場合は次の形式を使います。

```bash
pytest tests/test_logic.py
pytest tests/test_score.py
pytest tests/test_match_history.py
pytest tests/test_logic.py::test_generate_matches_uses_only_active_players_and_benches_over_capacity
```

次のような変更では、関連テストだけでなく原則として全テストを実行してください。

- 組み合わせ生成
- スコア計算
- Participant
- MatchSession
- MatchHistory / BenchHistory
- state 管理
- reset
- LINE 通知
- 履歴ダンプ
- config 読み書き
- 共通 utility
- route の共通処理

動作仕様を変更する場合は、対応するテストを追加または更新してください。

失敗理由を確認せずにテストを削除、skip、緩和してはいけません。

テストが失敗した場合、まず既存仕様の破壊なのか、テスト側が古いのかを調査して
ください。

単にテストを通す目的で既存の assertion を弱めないでください。


## Local Run Commands

リポジトリ直下から Flask の開発サーバーを起動します。

```bash
python app.py
```

ローカル URL は次のとおりです。

- Application: `http://localhost:5001`
- Participant view: `http://localhost:5001/viewer`
- Administrator view: `http://localhost:5001/admin`
- Administrator settings: `http://localhost:5001/admin/settings`
- Participant API: `http://localhost:5001/api/participants`
- Match state API: `http://localhost:5001/api/match_state`

UI を変更した場合は、参加者向けと管理者向けの両方を確認してください。

デスクトップ表示だけでなく、スマートフォン相当の狭い viewport でも操作と
表示を確認してください。


## Development Rules

- 明示的な仕様変更がない限り、既存仕様と既存データ形式を維持してください。
- Participant view と Administrator view は、既存の URL および `viewer` /
  `admin` mode によって分離したままにしてください。
- 管理者ログインや認証を、別の変更に付随する形で追加しないでください。
- 参加者向けの `index.html`、`register.html`、`match_result.html` は
  互換性を重視してください。
- route、form field、主要な表示動作を不用意に変更しないでください。
- UI はスマートフォン利用を前提に設計してください。
- 狭い画面でも文字、カード、組み合わせ、form、button が重なったり画面外に
  隠れたりしないことを確認してください。
- 新しい framework や抽象化を導入する前に、既存の Flask、SQLAlchemy、
  Jinja、`utils/` の実装パターンを優先してください。
- route、model、state、template の変更は一貫させてください。
- 保存形式や form field を変更する場合は、すべての読み書き箇所を確認し、
  回帰テストを追加してください。
- 無関係な dependency update は行わないでください。
- 新しい依存関係が必要な場合だけ `requirements.txt` を更新し、version を
  固定してください。
- cache、ローカル DB、実行時 state、dump ファイルを source code の変更として
  編集または commit しないでください。


## Refactoring Rules

リファクタリングでは、明示的な仕様変更の指示がない限り、外部から観測可能な
動作を変更しないでください。

特に以下を維持してください。

- URL / route
- redirect 先
- form field
- JSON API response
- DB schema
- config schema
- `match_state.json` schema
- `draft_state.json` schema
- 組み合わせ生成に関する既存仕様
- score 入力仕様
- LINE 通知対象
- LINE 通知タイミング
- LINE 通知の重複防止仕様
- 履歴 dump の内容
- reset 時のデータ整合性
- template から参照される変数
- 既存 test が保証している動作

コードを整理できるという理由だけで、次のような大規模な設計変更を導入しないで
ください。

- 新しい Web framework
- repository layer
- service layer
- dependency injection framework
- 新しい ORM
- 新しい state store
- 新しい database
- async 化
- application factory 化

大きな設計変更が有効だと判断した場合は、依頼されたタスクへ勝手に含めず、
作業完了時に改善候補として報告してください。

リファクタリングでは、既存 test が通ることを最低条件とし、必要に応じて
リファクタリング前後で test 結果を比較してください。


## Match Generation Rules

組み合わせ生成は、明示的な仕様変更がない限り既存の公平性ルールを維持して
ください。

主な前提は次のとおりです。

- `active` な参加者のみを対象とする
- court count による同時出場人数を守る
- `games_played` を考慮して出場回数の公平性を維持する
- ベンチ対象者の公平性を維持する
- 過去の現役 DB 上の `MatchHistory` を利用し、過去に組んだペアを可能な範囲で
  回避する
- ペア作成時に player score / pair score を理由として既存仕様以上の決定的な
  ペア固定を行わない
- 完成したペア同士の対戦調整では既存の pair score の扱いを維持する
- draft 編集中の `fixed_pairs` を尊重する
- `fixed_pairs` は現在の draft のみで有効とする

組み合わせ生成アルゴリズムを整理する場合は、ランダム性を不用意に失わせないで
ください。

テストを安定させる目的だけで生成結果を固定化してはいけません。


## Persistent State and Data Stores

現在のアプリでは、複数の永続データ・実行時 state を用途別に使用しています。

用途を混同しないでください。


### SQLite Database

通常は次の場所に SQLite DB を持ちます。

```text
instance/participants.db
```

DB は参加者だけではなく、試合履歴、セッション、LINE 通知関連情報も保持します。

主な責務は次のとおりです。

#### Participant

以下の参加者情報は DB を正とします。

- name
- card
- gender
- level
- weight
- active
- games_played

#### Match History

確定した試合履歴は次のモデルで管理します。

- `MatchRound`
- `MatchHistory`
- `BenchHistory`

ダンプ済み JSON を現在の組み合わせ生成履歴として読み戻してはいけません。

過去ペア回避等で使用する履歴は、現役 DB 上の履歴を基準とします。

#### Match Session

現在の組み合わせセッションは `MatchSession` で管理します。

session の作成、reset、confirm、revert 等では JSON state と DB session の
整合性を維持してください。

#### LINE Notification State

以下は DB で管理します。

- `LineAccount`
- `NotificationSubscription`
- `LineLinkToken`
- `MatchNotification`
- `NotificationDeliveryLog`

これらのデータを JSON state や Flask session へ移さないでください。


### `match_state.json`

確定済みの現在の組み合わせ状態を保持する共有 state です。

主に次を保持します。

- match active 状態
- match count
- 確定済み matches
- 確定済み bench
- timestamp
- session id

確定済みの組み合わせ、待機者、試合回数、試合の有効状態については
`match_state.json` を正とします。

draft の作成や編集によって、最後に確定した `match_state.json` を上書きしては
いけません。


### `draft_state.json`

生成後、確定前に編集中の組み合わせを保持する共有 draft state です。

主に次を保持します。

- draft 状態
- matches
- bench
- court count
- fixed pairs
- timestamp

未確定の draft 組み合わせと待機者については `draft_state.json` を正とします。

`draft_state.json` を確定履歴として扱ってはいけません。

`fixed_pairs` は現在編集中の draft にのみ有効です。

confirm 後や次の新規生成へ不用意に引き継がないでください。


### Flask Session

Flask session は client 単位です。

業務状態の共有 store として使用してはいけません。

Flask session に保存してよい値は、原則として `flash()` が使用する一時通知の
`_flashes` のみです。

以下を Flask session に再導入しないでください。

- `draft_matches`
- `draft_bench`
- `court_count`
- `last_confirmed_*`
- その他の共有業務状態


### `config.json`

`config.json` は環境ごとのアプリ設定を保存します。

主な設定には次のようなものがあります。

- PayPay 支払い URL
- PayPay URL 有効期限
- level 設定
- gender weight
- score input mode
- scoring settings
- deuce settings
- consecutive play limit
- history dump email settings

`config.example.json` は公開可能な設定例です。

実際の PayPay URL、個別の送信先等を `config.example.json` へコピーしないで
ください。

設定 schema を変更する場合は、既存 `config.json` が読み込める互換性を考慮して
ください。


### `instance/history_dumps/`

確定済みの過去試合履歴を JSON dump として保存します。

この directory は Git 管理対象外です。

履歴 dump はバックアップ・参照用であり、現在の組み合わせロジックが参照する
現役 DB 履歴とは区別してください。


## State Management Rules

state を扱う変更では必ず次のルールを守ってください。

- JSON state 内の参加者は participant ID で保持します。
- 表示時は DB から `Participant` を解決してください。
- draft の編集で confirmed state を破壊しないでください。
- confirm は同じ round を二重に確定しないでください。
- confirm 時は DB 履歴、`games_played`、match state、session state の整合性を
  維持してください。
- revert 時は対象 round と関連する履歴を整合した状態で巻き戻してください。
- reset 処理では DB session、JSON state、`games_played`、通知 subscription、
  画面表示の整合性を維持してください。
- JSON schema を変更する場合は、既存ファイルを読める互換処理または migration
  を用意してください。
- `match_state.json` と `draft_state.json` の実行時変更を commit しないで
  ください。


## LINE Messaging Rules

LINE Messaging 機能は参加者ごとの試合通知に使用します。

明示的な仕様変更がない限り、既存の通知フローを維持してください。

### Feature Flag

`LINE_MESSAGING_ENABLED` が有効な環境でのみ LINE Messaging 機能を有効にします。

開発環境で LINE 関連 credential が設定されていない場合でも、LINE 無効状態で
アプリの通常機能が動作する状態を維持してください。


### Account Linking

参加者と LINE user ID の紐付けには `LineAccount` を使用します。

連携コードは `LineLinkToken` で管理します。

token の以下の性質を壊さないでください。

- 有効期限
- 使用済み判定
- participant との対応
- session との対応
- token の一意性


### Subscription

組み合わせ通知の登録状態は `NotificationSubscription` で管理します。

subscription は session 単位です。

過去 session の subscription を現在の session の通知対象として扱っては
いけません。


### Match Notification

組み合わせ確定後の通知は参加者ごとに内容が異なります。

出場者には、自分の試合について以下を通知します。

- court number
- 同じ court の参加者
- 自分が属する team
- match count 等の必要な情報

bench の参加者には待機通知を送信します。

参加者が matches にも bench にも存在しない場合は、既存仕様に従って通知対象外と
してください。


### Duplicate Prevention

同一 session / match count / channel の通知を二重送信してはいけません。

`MatchNotification` による既存の重複防止仕様を維持してください。

参加者ごとの送信結果は `NotificationDeliveryLog` へ記録します。

一部の通知送信が失敗した場合も、既存の partial failure の扱いを維持して
ください。


### LINE Webhook

`/line/webhook` では LINE の署名を検証してください。

署名検証、token 検証、participant active 判定等を弱めないでください。

LINE credential、user ID、token を不要に log 出力しないでください。


## Match History and Score Rules

試合履歴は `MatchRound`、`MatchHistory`、`BenchHistory` を中心に管理します。

score 入力には既存の設定に従って以下のモードがあります。

```text
winner_only
score
```

score mode の point 数、game 数、deuce、maximum point 等の設定を変更する場合は、
score validation と winner 判定を同時に確認してください。

保存済み履歴の score や winner の意味をリファクタリングによって変更しては
いけません。


## History Dump Rules

履歴 dump では、確定済み履歴を JSON として
`instance/history_dumps/` へ保存します。

履歴削除を伴う操作では、既存の best-effort backup の考え方を維持してください。

JSON 保存やメール送信に失敗した場合の扱いを変更する場合は、データ削除処理との
関係を必ず確認してください。

dump schema を変更する場合は、既存の dump 表示・test・運用への影響を確認して
ください。


## History Dump Email Rules

設定で有効になっている場合、履歴 dump を SMTP メールで送信します。

- 有効 / 無効は既存設定に従う
- recipient は設定から取得する
- SMTP 接続情報や credential は環境変数から取得する
- password を config や source code に保存しない
- dump 保存とメール送信の責務を混同しない
- メール送信失敗時の既存の best-effort 動作を維持する

メール機能を変更する場合は、メールが無効な環境でも履歴 dump が正常に動作する
ことを確認してください。


## PayPay Link Rules

PayPay 支払いリンクとその有効期限は設定で管理します。

実際の PayPay URL を repository に commit してはいけません。

期限警告の表示条件を変更する場合は、期限当日、前日、期限切れ、未設定の
境界条件を確認してください。


## Reset Rules

reset には異なる目的の操作があります。

通常の match reset と全データ削除を混同しないでください。

reset を変更する場合は少なくとも以下を確認してください。

- current `MatchSession`
- new session 作成の有無
- `match_state.json`
- `draft_state.json`
- `games_played`
- MatchHistory
- BenchHistory
- NotificationSubscription
- LINE account を残すか削除するか
- history dump の有無
- UI に表示される現在状態

既存の reset 処理を単純化する際、対象データを広げたり狭めたりしないでください。


## Manual Site Rules

`manual-site/` はアプリ本体とは別に、参加者・管理者向けの利用方法を説明する
コンテンツです。

manual site を変更する場合は、実際のアプリ UI、route、操作手順と一致している
ことを確認してください。

アプリの仕様変更を行った場合、マニュアルへの影響があるか確認し、必要であれば
更新候補として報告してください。

マニュアルの更新だけを理由にアプリ本体の仕様を変更してはいけません。


## Security / Data Handling Rules

- 実在する参加者情報を commit しないでください。
- ローカル SQLite DB を commit しないでください。
- secret、credential、token を commit しないでください。
- 非公開 URL、個別の決済情報を commit しないでください。
- `SECRET_KEY` は環境変数から設定してください。
- 本番環境では推測困難な `SECRET_KEY` を必須とします。
- `ALLOW_DEV_SECRET_KEY=1` はローカル開発でのみ使用してください。
- 実際の PayPay リンクを commit しないでください。
- `config.json`、`match_state.json`、`draft_state.json`、`*.db`、
  `instance/history_dumps/` はローカルまたは実行時データとして扱ってください。
- 明示的に必要な機能でない限り、参加者情報を log、debug output、template、
  API に新しく露出させないでください。
- route の境界で入力値を検証してください。
- CSV row、参加者項目、card、level、gender は保存前に検証してください。
- DB 操作には SQLAlchemy または parameterized SQL を使用し、ユーザー入力から
  SQL 文字列を組み立てないでください。
- upload された CSV は信頼できない入力として扱ってください。
- production を意識した変更で入力検証を弱めたり、危険な debug 設定を
  有効化したりしないでください。
- 管理者ログインが不要なのは現在の製品仕様です。Administrator endpoint が
  public internet に安全に公開できることを意味しません。


## Dependency Rules

現在使用している framework・library を理由なく置き換えないでください。

dependency の追加・更新が必要な場合は以下を確認してください。

- 本当に標準 library や既存 dependency では実現できないか
- production 環境への影響
- Python version との互換性
- requirements への version pin
- test 環境への影響

依頼された作業と無関係な dependency upgrade をまとめて行わないでください。


## Platform Migration Rules

Cloudflare 等の別 platform への移行が将来的に予定されていますが、
明示的に migration task として依頼されるまでは platform 固有コードを追加
しないでください。

通常のリファクタリングを、将来の移行を理由として過剰に抽象化しないでください。

platform migration を実施する場合も、既存アプリの仕様を基準として段階的に
移行してください。

特に次の変更は独立した設計変更として扱ってください。

- SQLite 以外への database 移行
- SQLAlchemy 以外の DB access 導入
- `match_state.json` の別 store への移行
- `draft_state.json` の別 store への移行
- `config.json` の別 store への移行
- history dump storage の変更
- SMTP 以外のメール送信方式
- Flask runtime の変更

これらを通常の cleanup や小規模 refactoring に混在させないでください。


## Review Guidelines

Review では style より先に以下を確認してください。

1. bug
2. 既存仕様の破壊
3. state の不整合
4. data loss
5. data leak
6. 通知の二重送信
7. 不足している test
8. security regression

最低限、次の項目を確認します。

- 変更元と Pull Request の対象が `develop` であること
- `main` への直接変更ではないこと
- `pytest` が成功していること
- 変更した動作に対応する回帰テストがあること
- 参加者登録、編集、`active` 状態更新が引き続き動作すること
- 組み合わせ生成が active、court count、bench、games played の公平性を
  維持していること
- 過去ペア回避の既存仕様を維持していること
- draft 編集が confirmed match state を破損または置換しないこと
- confirm が DB history、match state、games played を一度だけ更新すること
- revert が対応する round を正しく巻き戻すこと
- reset が session、JSON state、試合回数等を整合した状態にすること
- LINE 通知が session / match count 単位で重複送信されないこと
- LINE 無効環境で通常機能が壊れないこと
- history dump が既存形式と運用を維持していること
- dump / email failure 時の既存動作を維持していること
- `/viewer` と `/admin` で、それぞれ適切な操作だけが表示されること
- `index.html`、`register.html`、`match_result.html` が正常に機能すること
- スマートフォン相当の画面で text、button、card、操作領域が重ならないこと
- 実データ、DB、state file、dump file、secret、PayPay リンクが diff に
  含まれないこと
- 新しい route と API が入力を検証し、不要な情報を返していないこと
- `README.md`、`AGENTS.md`、manual site、setup 手順が実装と一致していること


## Completion Checklist

コード変更を完了する前に、可能な範囲で次を実行してください。

```bash
pytest
git diff --check
git status
git diff
```

作業完了時は次を簡潔に報告してください。

- 何を調査したか
- 何を変更したか
- 変更したファイル
- 実行した test
- test 結果
- 手動で確認した UI
- 実行できなかった確認
- 残っている課題
- 今回あえて変更しなかった設計上の課題

ユーザーから明示的な指示があるまでは commit / push / PR 作成を行わないで
ください。
