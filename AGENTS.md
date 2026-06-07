# AGENTS.md

## Project Overview

Shuttlers Match App は、バドミントンイベントの参加者管理とダブルスの
組み合わせ生成を行う Python 3.10+ / Flask アプリケーションです。

主な構成は次のとおりです。

- `app.py`: Flask アプリケーションのエントリポイント、画面ルート、主要処理
- `models.py`: Flask-SQLAlchemy の `Participant` モデル
- `logic.py`: ダブルスの組み合わせ生成ロジック
- `routes/api.py`: 参加者情報と試合状態を返す JSON API
- `utils/`: 状態管理、スコア計算、DB 操作、リセット処理
- `templates/`: Jinja テンプレート
- `static/`: カード画像と CSV テンプレート
- `tests/`: pytest による自動テスト

参加者向け画面と管理者向け画面は URL で分離されています。

- Participant view: `/viewer`（`/` からリダイレクト）
- Administrator view: `/admin`
- Administrator settings: `/admin/settings`

現時点では管理者ログインは不要という仕様です。明示的な仕様変更の依頼が
ない限り、認証機能を追加したり、参加者向け画面と管理者向け画面を統合したり
しないでください。

## Branch Policy

- 開発の基準ブランチは `develop` です。
- 作業開始前に現在のブランチと `git status` を確認してください。
- 通常の機能追加や修正は、最新の `develop` から作業ブランチを作成します。
- `main` に直接 commit または push しないでください。
- Pull Request の通常の対象ブランチは `develop` です。
- リポジトリ管理者から明示的な指示がない限り、`main` 向けの変更を作成しないで
  ください。
- commit は依頼された作業単位に絞り、無関係なリファクタリング、整形、
  生成データ、ローカル環境の変更を混在させないでください。
- ユーザーによる未 commit の変更を破棄、上書き、巻き戻ししないでください。

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

`config.example.json` は公開可能な設定例です。実際の PayPay リンクや
環境固有の値は、Git 管理対象外の `config.json` にだけ保存してください。

## Test Commands

リポジトリ直下で全テストを実行します。

```bash
pytest
```

pytest の設定は `pytest.ini` にあります。`tests/` 配下の `test_*.py` と
`test_*` 関数がテスト対象です。

個別に実行する場合は次の形式を使います。

```bash
pytest tests/test_logic.py
pytest tests/test_score.py
pytest tests/test_logic.py::test_generate_matches_uses_only_active_players_and_benches_over_capacity
```

組み合わせ生成、スコア計算、参加者状態、共通 utility を変更した場合は、
必ず全テストを実行してください。動作仕様を変更する場合は、対応するテストを
追加または更新してください。失敗理由を確認せずにテストを削除、skip、緩和
してはいけません。

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
  互換性を重視してください。route、form field、主要な表示動作を壊さないで
  ください。
- UI はスマートフォン利用を前提に設計してください。狭い画面でも文字、
  カード、組み合わせ、form、button が重なったり画面外に隠れたりしないことを
  確認してください。
- 新しい framework や抽象化を導入する前に、既存の Flask、SQLAlchemy、
  Jinja、`utils/` の実装パターンを優先してください。
- route、model、state、template の変更は一貫させてください。保存形式や
  form field を変更する場合は、すべての読み書き箇所を確認し、回帰テストを
  追加してください。
- 組み合わせ生成は、明示的な仕様変更がない限り、`active` な参加者のみを
  対象とし、コート数による定員と `games_played` による公平性を維持して
  ください。
- 無関係な dependency update は行わないでください。新しい依存関係が必要な
  場合だけ `requirements.txt` を更新し、version を固定してください。
- cache、ローカル DB、実行時 state を source code の変更として編集または
  commit しないでください。

## State Management Rules

このアプリには責務の異なる 3 種類の state store があります。用途を混同
しないでください。

- `instance/participants.db`: 参加者の氏名、カード、性別、レベル、weight、
  `active`、`games_played` を保存する SQLite DB
- `match_state.json`: 確定済みの組み合わせ、待機者、試合回数、試合の有効状態
  を保存する共有 state
- `draft_state.json`: 生成後、確定前に編集中の仮組み合わせと待機者だけを保存
  する draft state

必ず次のルールを守ってください。

- 確定済みの組み合わせは `match_state.json` に保存します。
- draft の作成や編集によって、最後に確定した `match_state.json` の内容を
  上書きしてはいけません。
- `draft_state.json` を確定履歴として扱ってはいけません。
- 組み合わせ確定時は、編集中の内容を confirmed state に反映し、draft を
  inactive にするか、適切に削除してください。
- reset 処理では、Flask session、JSON state、参加者の `games_played`、
  画面表示の整合性を維持してください。
- JSON schema を変更する場合は、既存ファイルを読める互換処理または migration
  を用意してください。
- JSON state 内の参加者は ID で保持します。表示時は DB から Participant を
  解決してください。
- Flask session は client 単位ですが、JSON state file はアプリ全体で共有
  される点に注意してください。
- `match_state.json` と `draft_state.json` の実行時変更を commit しないで
  ください。

## Security / Data Handling Rules

- 実在する参加者情報、ローカル SQLite DB、secret、credential、token、
  非公開 URL、決済情報を commit しないでください。
- 実際の PayPay リンクを commit しないでください。`config.json` に保存し、
  `config.example.json` には placeholder だけを記載してください。
- `config.json`、`match_state.json`、`draft_state.json`、`*.db` はローカル
  または実行時データです。Git 管理対象外の状態を維持してください。
- 明示的に必要な機能でない限り、参加者情報を log、debug output、template、
  API に新しく露出させないでください。
- route の境界で入力値を検証してください。CSV row、参加者項目、card、
  level、gender は保存前に検証します。
- DB 操作には SQLAlchemy または parameterized SQL を使用し、ユーザー入力から
  SQL 文字列を組み立てないでください。
- upload された CSV は信頼できない入力として扱い、任意の内容を実行したり、
  検証せず保存したりしないでください。
- production を意識した変更で入力検証を弱めたり、危険な debug 設定を
  有効化したりしないでください。
- 管理者ログインが不要なのは現在の製品仕様です。Administrator endpoint が
  public internet に安全に公開できることを意味しません。

## Review Guidelines

Review では style より先に、bug、既存仕様の破壊、state の不整合、data leak、
不足している test を確認してください。

最低限、次の項目を確認します。

- 変更元と Pull Request の対象が `develop` であり、`main` への直接変更では
  ないこと
- `pytest` が成功し、変更した動作に対応する回帰テストがあること
- 参加者登録、編集、`active` 状態の更新が引き続き動作すること
- 組み合わせ生成が `active`、court count、bench、`games_played` の公平性を
  維持していること
- draft の編集が確定済みの match state を破損または置換しないこと
- 確定操作が `match_state.json` と `games_played` を一度だけ更新すること
- reset が対象となる session、JSON state、試合回数を整合した状態にすること
- `/viewer` と `/admin` で、それぞれ適切な操作だけが表示されること
- `index.html`、`register.html`、`match_result.html` が正常に機能すること
- スマートフォン相当の画面で text、button、card、操作領域が重ならないこと
- 実データ、DB、state file、secret、PayPay リンクが diff に含まれないこと
- 新しい route と API が入力を検証し、不要な情報を返していないこと
- `README.md`、setup 手順、実行コマンドが実装と一致していること

作業完了時は、変更した file、実行した test、手動で確認した UI を簡潔に報告
してください。実行できなかった test や確認項目がある場合は明記してください。
