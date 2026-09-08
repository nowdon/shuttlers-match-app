# pytest の実行時データ隔離

`tests/conftest.py` の `isolated_runtime` は全テストに自動適用される。
app を import したり DB を作成したりする fixture ではなく、テストが
Flask / SQLAlchemy を使用するときの保存先を隔離する。

```text
pytest のテストごとの tmp_path/
├── instance/
│   ├── participants.db
│   └── history_dumps/
├── config.json
├── match_state.json
└── draft_state.json
```

ファイルやディレクトリは実際に必要になった時点で作成される。
DB 単体テストの明示的な一時 SQLite ファイルと in-memory DB も維持する。

## 修正前の問題

Flask の既定の `instance_path` は app モジュールの位置から決まるため、
`monkeypatch.chdir(tmp_path)` だけでは変わらない。app.py はこのパスから
DB URI を作り、import 中の `db.init_app()` で engine を生成する。
後から config の URI だけを変更しても engine は移動しない。

| テスト / fixture・helper | 修正前の影響 |
| --- | --- |
| `test_match_history.py::load_history_test_app` | 実 DB の drop/create と履歴・参加者・通知データの書き込み。dump、dump_and_clear、reset_db、archive テストは実 instance 配下に JSON を作成していた |
| `test_line_notification_routes.py::load_test_app`（`app_module` fixture） | 実 DB の drop/create、参加者・LINE・session データの書き込み |
| `test_stats.py::load_stats_test_app` | 実 DB の drop/create、参加者・試合履歴の書き込み |
| `test_match_draft.py::load_test_app` | initialize_runtime による実 DB の schema 初期化・互換処理 |
| `test_secret_key.py::import_app_with_config` | secret が有効なテストで initialize_runtime / WSGI 経由の実 DB 初期化 |
| `test_flash_messages.py::import_app` | リクエスト時の実 DB 初期化。DB をモックしていない route は実 DB を参照 |
| `test_paypay_expiration.py::import_app` | 画面を要求するテストで WSGI 経由の実 DB 初期化 |

`test_runtime_initialization.py::runtime_app` は既に Flask 作成時に
instance_path を隔離していた。今回はその責務を共通 fixture へ移した。
import-only subprocess テストは、既存の I/O 禁止ガードを維持している。

`test_logic.py`、`test_match_session.py`、`test_notification_models.py`、
`test_data_access.py` の DB fixture は既に一時 SQLite または in-memory DB を
使っていた。ただし logic の一部は cwd にある config/state を読めたため、
共通 cwd 隔離で開発者の設定への依存も解消する。
`test_reset.py` は DB をモックしており、state 書き込みテストも一時 cwd を使用済み。
match_state / draft_state の書き込みテストも一時 cwd を使用済み。
route 登録の検証は import のみで runtime 初期化を行わない。

## 共通 fixture の責務

1. app / init_db / route モジュールのキャッシュと親 package の参照を除去する。
2. cwd を tmp_path に移し、開発用 secret opt-in と外部サービス用の環境を隔離する。
   個別の secret / LINE / SMTP テストは必要なテスト値を上書きする。
3. `Flask.__init__` を patch し、instance_path を一時ディレクトリへ指定する。
   collection 時に `from flask import Flask` した alias にも適用される。
4. `SQLAlchemy.init_app` の前に instance と SQLite URI が一時領域内か検証する。
   app.py 自身が隔離済み instance から絶対 URI を生成するため、本番コードの変更は不要。
5. 個別テストが必要に応じて app を新規 import / 初期化する。
6. teardown で生成された全 app の scoped session を remove し、engine を dispose する。
   テスト途中で module cache から外された app も保持して後始末する。
   最後に module cache を消去し、monkeypatch が cwd・環境・patch を復元する。

config/state の相対パスは一時 cwd、dump は隔離された
`app.instance_path/history_dumps` に解決される。
純粋な単体テストには app import、DB 初期化、config 生成を強制しない。

## 回帰検証

`test_runtime_isolation.py` は実 engine の DB パス、再 import 時の app 分離、
一時領域外の URI の初期化拒否を検証する。
さらにコードとテストを一時リポジトリへコピーし、実データをコピーせずに
DB・dump・config・state の保護用ファイルを配置する。
そのコピー内の subprocess で history / LINE / stats / reset / runtime /
draft / secret / flash / PayPay の既存テストを実行し、ファイル集合と SHA-256 が
変わらないことを検証する。実 DB の存在や内容には依存しない。

新しい integration test も共通 fixture の範囲内で app を作成すること。
subprocess は親 pytest の monkeypatch を継承しないため、子 pytest でこの
conftest を読み込むか、import-only 検証のように子プロセス内で I/O を禁止する。
本番データをテスト入力として使用しない。

PR #71 の検証時に確認された既存 pytest の副作用は当時の記録として維持する。
今回の変更はその後の隔離修正であり、既存 DB や dump の復元・削除は行わない。
