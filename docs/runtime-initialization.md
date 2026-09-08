# Runtime 初期化と import 時副作用

## 調査範囲・変更前 inventory

`app.py`、`models.py`、`logic.py`、`routes/`、`data/`、`utils/`、
`init_db.py` を対象に、検索と Python AST による module top-level 確認を実施。
関数内だけの処理と、top-level 呼び出しから到達する処理を分けた。

| 箇所 | 変更前の import 時処理 | 変更後 |
| --- | --- | --- |
| `app.py` Flask 生成・logging・config 代入 | Flask app 生成、Gunicorn logger の設定、DB URI 設定 | 維持。config 代入は `config.json` 読み込みではない |
| `app.py` SECRET_KEY | `get_secret_key()` が環境を検証。不足時例外、開発 opt-in 時 warning | import 時は環境値取得のみ。検証・開発 fallback は runtime |
| `app.py` DB extension | `db.init_app(app)` による engine / pool 設定 | 維持。絶対 SQLite パスのため instance directory 作成なし、接続なし |
| `app.py` DB 初期化 | `ensure_database_tables()` → `os.makedirs`、`db.create_all()` | `initialize_runtime()` から明示実行 |
| `app.py` schema 互換 | `inspect(db.engine)`、table / column 検査、不足時 `text` / `ALTER TABLE`、commit、OperationalError 時 rollback | 同じ関数・SQL・例外処理を runtime から実行 |
| `app.py` / routes | Blueprint 生成・登録、関数参照の import、decorator 登録 | 維持。import のみで全 endpoint 登録可能 |
| `routes/helpers.py` config | `load_config()` → `load_raw_config()` → `open(config.json)`。global config / LEVEL_MAP / GENDER_WEIGHT 固定 | 削除。登録と CSV 登録の request 内で現在値を取得 |
| `routes/helpers.py` その他 | カード一覧・定数・regex 構築、`ZoneInfo('Asia/Tokyo')` | 維持。ZoneInfo の timezone データ読み込みは残る |
| `models.py` | `SQLAlchemy()` と model / Column / relationship / index 定義 | 維持。モデル定義だけでは接続・query なし |
| `logic.py` / `data/` | import と関数定義 | query / state 読み込みは関数実行時のみ |
| `utils/config.py` | 定数・関数定義 | config 読み書きは関数実行時のみ |
| `utils/match_state.py` / `draft_state.py` | パス・初期値・sentinel 定義 | JSON 読み書きは関数実行時のみ |
| session / reset / stats / pair utilities | import と関数定義 | query、commit、rollback、state 操作は関数実行時のみ |
| LINE / SMTP utilities | 標準 library import、URL / 設定値定数 | urllib / SMTP 接続・送信は関数実行時のみ |
| `init_db.py` | import と無条件 `ensure_database_tables()` | main guard 内で `initialize_runtime()` を再利用 |

## 実行タイミング

- `initialize_runtime()` は SECRET_KEY 検証後、既存 DB 初期化関数を呼ぶ。
- 成功フラグと lock により、各アプリプロセスで一度だけ実行する。
  DB 初期化に失敗した場合は例外を伝播し、成功扱いにしない。
- Gunicorn の標準 `gunicorn.conf.py` の `post_worker_init` は worker ごとに実行。
  `app:app` を変更しない。標準設定をロードする構成が前提。
- 明示初期化なしの WSGI では、最初の request の session 作成前に実行。
  毎 request の table 作成・schema 検査は行わない。
- `python app.py`、`python init_db.py`、Flask CLI `init-runtime` も同じ関数を使用。
- `ensure_database_tables()` 自体は既存互換テストや明示 DB 再確認用に残す。
- `/admin/reset_db` の既存 `db.create_all()` は全データ削除操作の処理として維持。
  今回、reset の transaction や処理内容は変更していない。

## Cloudflare 次フェーズに残る障壁

この表はコードとローカル CPython の確認結果に基づく。Workers での可用性は未検証。

| 依存 | import 時 | runtime / request 時 |
| --- | --- | --- |
| Flask-SQLAlchemy / SQLAlchemy | package が必要。engine / pool 構築は残る | ORM・接続・transaction が必要 |
| models / sqlite driver | model 定義と `db.init_app` 経由の sqlite DBAPI import が残る。sqlite driver 不在なら import 障壁 | ローカル SQLite と既存 schema 初期化が必要 |
| filesystem | Python module / template パス解決、timezone データ参照は残る。アプリの config / state 読み込み・書き込みなし | DB、config、state、template、static、history dump に依存 |
| ZoneInfo | `Asia/Tokyo` データ解決が残る。timezone database / tzdata 不在なら import 障壁 | 日付計算に使用 |
| routes/helpers | config I/O は削除。models・timezone・LINE・SMTP 等の依存を import | DB・config・state・描画・履歴 dump が必要 |
| LINE utilities | urllib / ssl 等の標準 library import が残る。接続・送信なし | 有効設定下で urllib によるネットワーク通信 |
| SMTP utilities | smtplib / ssl 等の import が残る。接続・送信なし | メール有効時に SMTP / socket 通信・添付ファイル読み込み |
| SECRET_KEY | 不足だけでは import 失敗しない | runtime では必須。開発 opt-in の意味は従来どおり |

Python 自体の通常の `.pyc` キャッシュ生成はアプリの業務 I/O と別。
書き込みを禁止する import-only テストでは `-B` / `PYTHONDONTWRITEBYTECODE=1` を使用する。
Cloudflare 固有の分岐・依存追加、保存先移行、application factory 化は実施していない。
