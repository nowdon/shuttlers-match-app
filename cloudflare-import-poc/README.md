# 現行 app import PoC

2026-09-08、`origin/develop` の `6b349d6` から
`codex/cloudflare-import-app-poc` を作成して検証。
**CPythonでは成功。local Worker / workers.devではtimezone data不足で失敗。**
現行アプリの修正、runtime初期化、アプリへのHTTP requestは行わない。

## 構成と再現

`src/worker.py` は `workers.WorkerEntrypoint` で診断JSONだけを返す。
FlaskのWSGI adapterには接続していない。
`src/diagnostics.py` は最初に `from app import app` を実行し、
例外を捕捉した後で各依存を個別に確認する。個別probeは同じinterpreter内で
動き、成功済みmoduleはPythonのimport cacheを利用する。

`src/` の `app.py`、`models.py`、`logic.py` と `data/`・`routes/`・`utils/` 内の
各 `.py` はリポジトリ本体への相対symlink。コピーやsys.pathの上書きは不要。
Wrangler 4.129.1ではdirectory symlinkの中身が収集されなかったため、
実directory内にfile単位のsymlinkを置いた。dry-runで全26個の本体Python sourceと
診断moduleの同梱を確認。ソース追加時にはsymlinkの追加が必要（テストで検出）。
実データ、設定、state、templates、staticは同梱していない。

依存はPoC専用 `pyproject.toml` にFlask 3.0.3 / Flask-SQLAlchemy 3.1.1を指定。
SQLAlchemyはWorker用 `pylock.toml` で2.0.52に解決。`uv.lock` はCLI環境用。
tzdataは今回追加せず、現状の障壁を記録した。

uvとNode/npmをPATHに置き、このディレクトリから実行する。

```bash
uv run pywrangler dev --ip 127.0.0.1 --port 8788
python smoke_http.py http://127.0.0.1:8788
```

CPythonではリポジトリの既存依存をインストールしたPythonで実行する。

```bash
env -u SECRET_KEY -u ALLOW_DEV_SECRET_KEY python -B src/diagnostics.py
# 詳細tracebackはローカルCLIのみ。Workerには公開しない。
env -u SECRET_KEY -u ALLOW_DEV_SECRET_KEY python -B src/diagnostics.py --tracebacks
```

公開応答に例外本文・traceback・path・secret値は含まれない。
`/cloudflare-import-poc/diagnostics` は診断失敗時もHTTP 200で結果を返す。
`/cloudflare-import-poc/health` は最初のapp import成功時だけ200、それ以外は404。
それ以外のパスもFlaskへ渡さず404。smokeはこの2つのPoC専用パスだけを呼ぶ。

## 実測結果

| 対象 | local CPython | local Worker | workers.dev |
| --- | --- | --- | --- |
| Flask | OK | OK | OK |
| Flask-SQLAlchemy | OK | OK | OK |
| SQLAlchemy | OK | OK | OK |
| sqlite3 | OK | OK | OK |
| models / db object | OK | OK | OK |
| ZoneInfo("Asia/Tokyo") | OK | ZoneInfoNotFoundError | ZoneInfoNotFoundError |
| urllib.request | OK | OK | OK |
| ssl | OK | OK | OK |
| smtplib | OK | OK | OK |
| routes.helpers | OK | ZoneInfoNotFoundError | ZoneInfoNotFoundError |
| app / app object | OK | ZoneInfoNotFoundError | ZoneInfoNotFoundError |

失敗箇所は `app.py:183` → `routes/helpers.py:96` の
`TOKYO_TZ = ZoneInfo("Asia/Tokyo")`。module import自体とtimezoneの構築は別で、
後者が失敗する。最初のapp importと依存診断の両方で同じ例外を実測した。
zoneinfoはsystem IANA dataまたはtzdataを探し、どちらもなければこの例外になる。
最小の次候補はPoC依存へのtzdata追加とそのdata fileの同梱確認であり、
本体へImportError回避や固定UTC offsetを追加することではない。
この解消後にapp全体が成功するかはまだ未検証。

CPythonでのBlueprintは `admin, api, history, line, match, participant`。
SECRET_KEYを外した新規processで `app.secret_key is None`、
`_runtime_initialized == False` を確認した。
Workerではapp import未完了のため完全なapp object/Blueprint一覧は未取得。

全3環境でaudit hookによるSQLite接続・runtime file open・socket接続、
profile hookによるinitialize_runtime呼出しの検出件数は0。
これらを試みた場合は実行前に例外で遮断する。SQLAlchemy engineの構成は
import時に行われるが、接続はしない。これは一般的な全I/O監査の保証ではなく、
今回の現行コードと記載したhook対象に対する観測。
sqlite3のimport成功は永続SQLiteやSQL実行の利用可能性を意味しない。
urllib/ssl/smtplibのimport成功もLINE/SMTP通信の実行可能性を意味しない。

安全な実測JSONは `results/cpython.json`、`results/local-worker.json`、
`results/workers-dev.json`。local Workerとworkers.devのJSONは完全一致。
公開直後の初回smokeは非200で失敗したが、その後curlで200、再実行のsmokeで
diagnostics=200 / health=404を確認した。初回の非200の原因は未確定。

## Cloudflare

- Worker: `shuttlers-match-import-poc`（新規。事前versions listで10007を確認）
- compatibility date: `2026-09-08` / flag: `python_workers`
- Worker Python: 3.14.2 / Pyodide: 314.0.6（両環境の応答で確認）
- CLI: uv 0.12.10 / workers-py (pywrangler) 1.17.2 / Wrangler 4.129.1
- SDK: workers-runtime-sdk 1.8.2 / CLI用CPython: 3.14.7
- アプリ検証用CPython: 3.12.0
- Local: `http://127.0.0.1:8788/cloudflare-import-poc/diagnostics`
  （検証後にローカルserverは停止済み）
- Deploy: <https://shuttlers-match-import-poc.nowdon.workers.dev/cloudflare-import-poc/diagnostics>
- Deploy version: `b4eea080-1705-4713-ae88-ea70dc9e86df`
- dry-run: 543 modules、gzip 2382.26 KiB、bindingなし
- Worker startup: 4113 ms（今回の診断はapp再試行も含む測定）

既存OAuth認証でdeployした。本番domain/DNS/EC2や既存
`shuttlers-match-flask-poc` は変更していない。再deployする場合:

```bash
uv run pywrangler whoami
uv run pywrangler deploy --dry-run
uv run pywrangler deploy
python smoke_http.py https://shuttlers-match-import-poc.nowdon.workers.dev
```

## 既存アプリとテスト

- baseline `pytest`: 311 passed。
- 追加後 `pytest`: 315 passed。新規テストは新規processでの副作用なしimport、
  例外情報の秘匿、runtime file読込の遮断、source link/binding境界の4件。
- `python -m compileall .`: exit 0。
- `git diff --check`: 成功。新規ファイルも別途空白エラー確認。
- app.py / models.py / requirements.txt / 既存route / 既存PoC: 変更なし。
- 実DB・config・JSON state: 変更なし。既存pytestは従来どおり隔離test DBを使う。
- 現行アプリへの手動request・UI確認: 未実施（今回の禁止事項に従う）。
- commit / push / PR: 未実施。commit前にsymlinkとPoC専用lockfile・診断結果を確認。
- マニュアル/UIの操作仕様変更はないためmanual-site更新は不要。

## 次フェーズの優先順位（未実装）

1. PoCのみでtzdataを明示し、data同梱とapp import再診断を行う。
2. import成功後、engine構成と実際のDBアクセスを区別して永続DB移行を設計する。
3. config / confirmed state / draft state / dumpの永続保存と整合性を設計する。
4. runtime初期化とrequest実行、template/staticのbundleを独立したPoCで検証する。
5. LINE・SMTPの通信方式と通知重複防止・部分失敗の維持を別途検証する。

本体への小変更候補はtimezone生成の遅延化だが、利用時のdata不足は残るため
今回実装していない。factory化・ORM置換・認証追加も対象外。

## 公式資料

- [Python Workers / pywrangler](https://developers.cloudflare.com/workers/languages/python/)
- [パッケージ管理](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Wrangler bundle設定](https://developers.cloudflare.com/workers/wrangler/configuration/)
- [Python標準ライブラリ](https://developers.cloudflare.com/workers/languages/python/stdlib/)
- [zoneinfoのdata sources](https://docs.python.org/3/library/zoneinfo.html#data-sources)
