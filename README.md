# Shuttlers Match App 🏸

バドミントンイベントの参加者管理、コート数に応じたダブルス組み合わせ生成、試合結果・履歴管理を行う Flask ベースの Web アプリケーションです。

## 🔍 概要

v1.4.0 では、従来の参加者管理と組み合わせ生成に加えて、試合履歴、勝敗・スコア入力、履歴の JSON ダンプ、ダンプ済み履歴の参照、勝率によるプレイヤースコア補正に対応しています。

主な機能は次のとおりです。

- 参加者登録（名前、性別、競技レベル、カード）
- 参加者の有効 / 無効状態と試合回数の管理
- コート数に応じたダブルス組み合わせ生成
- 試合回数とプレイヤースコアを考慮した組み合わせ生成
- 未確定の仮組み合わせ編集
- 組み合わせ確定と試合結果表示
- 試合履歴管理
- 勝敗・スコア入力
- 履歴の JSON ダンプ
- ダンプ済み履歴の参照
- DB 上の入力済み試合履歴から算出した勝率によるプレイヤースコア補正
- 参加費案内と QR コード支払い
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

`config.json` はローカル環境ごとの設定ファイルです。Git 管理対象外のため、初回セットアップ時は `config.example.json` をコピーして必要に応じて編集してください。

`SECRET_KEY` は Flask の session cookie 署名に使います。`SECRET_KEY` が設定されている場合はその値を使用します。未設定の場合、デフォルトでは起動に失敗します。ローカル開発だけで固定 fallback を使いたい場合は、明示的に `ALLOW_DEV_SECRET_KEY=1` を設定してください。本番環境では必ず環境変数 `SECRET_KEY` に推測困難な値を設定し、`ALLOW_DEV_SECRET_KEY=1` は使わないでください。

## 🧭 状態管理と Flask session の方針

このアプリでは、業務状態の正本を client 単位の Flask session ではなく、用途ごとの共有 state store に分けて管理します。

- Flask session に保存してよい値は、`flash()` が使う一時通知の `_flashes` のみです。
- `match_state.json`: 確定済み組み合わせ、待機者、試合回数、試合の有効状態などの実行時状態を保存します。
- `draft_state.json`: 未確定の仮組み合わせと待機者を保存します。仮組み合わせの作成・編集で `match_state.json` を上書きしないための状態です。
- SQLite DB (`instance/participants.db`): 参加者、試合履歴、ベンチ履歴などを保存します。
  - 参加者の氏名、カード、性別、レベル、weight、`active`、`games_played` は DB を正とします。
  - 組み合わせ確定時の履歴は `MatchRound`、`MatchHistory`、`BenchHistory` として DB に保存されます。
- `instance/history_dumps/`: JSON ダンプされた過去履歴を保存します。Git 管理対象外です。
- `draft_matches`、`draft_bench`、`court_count`、`last_confirmed_*` を Flask session に再導入しないでください。

`SECRET_KEY` は Flask の session cookie 署名に使うため、環境変数 `SECRET_KEY` から設定します。本番環境では `SECRET_KEY` の設定を必須とし、推測困難な値を指定してください。ローカル開発でのみ、明示的に `ALLOW_DEV_SECRET_KEY=1` を設定した場合に固定 fallback の利用を許可します。本番環境では `ALLOW_DEV_SECRET_KEY=1` を使わないでください。

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
| `/participant/<card>` | 参加者情報の編集 |
| `/match` | 組み合わせ生成フォーム |
| `/match/edit` | 未確定の仮組み合わせ編集 |
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
- 試合結果の閲覧が可能
- 勝敗・スコア入力 UI は表示されません

### adminモード

http://localhost:5001/admin でアクセスします。

管理者向け機能は次のとおりです。

- 参加者管理（登録、編集、有効 / 無効状態の管理、CSV 取り込みなど）
- コート数設定
- 組み合わせ生成
- 仮組み合わせ編集
- 組み合わせ確定
- 確定済み組み合わせと試合結果の表示
- 試合結果入力
- 試合履歴表示
- 履歴ダンプ
- 履歴ダンプ後の DB 履歴消去
- ダンプ済み履歴表示
- 全データ削除時の履歴自動ダンプ
- スコア入力モード設定
- ブラウザ上での `config.json` 修正（`/admin/settings`）
- 参加者 DB の内容を全削除

## 📋 試合履歴機能（v1.4.0）

v1.4.0 では、組み合わせ確定後の履歴管理が強化されています。

- 組み合わせ確定時に、ラウンド単位の `MatchRound`、各コートの試合を表す `MatchHistory`、待機者を表す `BenchHistory` として DB に保存されます。
- `/admin/match_history` で、現在 DB に残っている現役履歴を確認できます。
- 現役履歴では勝敗・スコア入力ができます。
- ラウンド単位で複数コートの結果を一括保存できます。
- admin モードの `/match/result` からも、最新の確定済み組み合わせに対して結果入力できます。
- viewer モードの `/match/result` では結果入力 UI は表示されず、閲覧のみです。

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

組み合わせ生成時のプレイヤースコアは、競技レベル・補正値・勝率を使って計算されます。

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
- `config.json`、`match_state.json`、`draft_state.json`、`*.db`、`instance/history_dumps/` はローカルまたは実行時データです。実データや secret、決済リンクを Git に commit しないでください。

## 🧪 テスト

pytest 構成を用意しています。ロジック、状態管理、履歴管理、スコア計算、参加者情報を変更したら、PR作成前に以下を実行してください。

```bash
pytest
```

テスト設定は `pytest.ini` に集約しており、`tests/` 配下の `test_*.py` を対象にしています。

## 🗂 ディレクトリ構成（例）

```
shuttlers-match-app/
├── app.py
├── logic.py
├── models.py
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
│   ├── draft_state.py
│   ├── match_state.py
│   ├── match_io.py
│   ├── db_utils.py
│   ├── state_utils.py
│   ├── stats.py
│   └── score.py
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
