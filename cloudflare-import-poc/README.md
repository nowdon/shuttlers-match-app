# 現行 app import PoC — tzdata追加後

2026-09-08、最新 `origin/develop` (`b835cc4`) から
`codex/cloudflare-import-with-tzdata` を作成して検証。
**tzdata 2025.3追加により、CPython / local Worker / workers.devのすべてで
`ZoneInfo("Asia/Tokyo")`、`routes.helpers`、`from app import app` が成功した。**
本体ソースは変更していない。PoC診断ではruntime初期化や現行アプリへのHTTP requestを行っていない。
既存pytestのDB/dumpへの副作用は後述する。

## PR #70からの変更

PR #70ではWorker両環境で `app.py:183` → `routes/helpers.py:96` →
`TOKYO_TZ = ZoneInfo("Asia/Tokyo")` が `ZoneInfoNotFoundError` になった。
zoneinfo moduleのimportは可能でもtimezone dataがなく、app importを完了できなかった。
今回の変更はPoC専用dependencyと診断・検証・結果記録に限定した。
本体にtimezone fallbackや固定UTC offsetを追加していない。

Pyodide 314.0.6の公式package一覧にある **tzdata 2025.3** を明示的に固定した。
最新tzdataへの更新や性能最適化は今回の目的に含めない。

## 構成と再現

既存Worker名、diagnostic route、`src/` の本体への相対symlinkを再利用する。
`src/worker.py` は `workers.WorkerEntrypoint` で診断JSONだけを返す。
FlaskのWSGI adapterには接続していない。
`src/diagnostics.py` はaudit/profile hookを設定後、最初にtimezoneを構築し、
続いて `from app import app`、最後に既存11項目の個別probeを実行する。
timezoneとその診断に必要な標準module以外はapp importに先行して読み込まない。
個別probeは同じinterpreterのimport cacheを利用する。

`src/` の `app.py`、`models.py`、`logic.py` と `data/`・`routes/`・`utils/` 内の
各 `.py` は本体へのfile単位のsymlink。全26個の本体Python sourceを再利用し、
コピーやsys.pathの上書きは行わない。ソース追加時はsymlink追加が必要（テストで検出）。
実データ、設定、state、templates、staticは同梱していない。

依存はPoC専用 `pyproject.toml` のFlask 3.0.3 / Flask-SQLAlchemy 3.1.1 /
tzdata 2025.3。Worker用 `pylock.toml` のSQLAlchemyは2.0.52のまま。
`uv.lock` はローカルCLI環境用。既存packageのversion変更はない。

uvとNode/npmをPATHに置き、このディレクトリから実行する。

```bash
uv run pywrangler sync
uv run pywrangler deploy --dry-run --outdir /tmp/shuttlers-tzdata-bundle
uv run pywrangler dev --ip 127.0.0.1 --port 8788
python smoke_http.py http://127.0.0.1:8788
```

`uv run` が `uv.lock` を更新し、`pywrangler sync` がPyodide向け
`pylock.toml` を解決して `python_modules/` にvendorする。
`--upgrade` は使用せず、既存のlockを制約として維持した。
`pylock.toml` はPyodide CDNとPyPIのtzdata wheel URL・hashを記録する。
Wranglerは `python_modules/` のdata fileも自動同梱する。
実際のdry-run出力で次を確認した（生成bundleはGit対象外）。

- tzdata本体627ファイル、distribution metadata込み635ファイル。
- `python_modules/tzdata/zoneinfo/Asia/Tokyo`: 213 bytes、先頭 `TZif`。
- Worker応答の `tzdata_version`: `2025.3`。
- 手動dataコピー、`loadPackage()` 呼出し、timezone fallbackは不要。

CPythonでは既存アプリ用Pythonで実行する。

```bash
env -u SECRET_KEY -u ALLOW_DEV_SECRET_KEY python -B src/diagnostics.py
# 詳細tracebackはローカルCLIのみ。Workerには公開しない。
env -u SECRET_KEY -u ALLOW_DEV_SECRET_KEY python -B src/diagnostics.py --tracebacks
```

公開応答に例外本文・traceback・path・secret値を含めない方針を維持する。
`/cloudflare-import-poc/diagnostics` は診断失敗時も200で結果を返す。
`/cloudflare-import-poc/health` は最初のapp import成功時だけ200、それ以外は404。
その他のパスもFlaskへ渡さず404。smokeはこの2つのPoC専用パスだけを呼ぶ。

## 実測結果

| 対象 | CPython | local Worker | workers.dev |
| --- | --- | --- | --- |
| Flask | OK | OK | OK |
| Flask-SQLAlchemy | OK | OK | OK |
| SQLAlchemy | OK | OK | OK |
| sqlite3 | OK | OK | OK |
| models / db object | OK | OK | OK |
| ZoneInfo("Asia/Tokyo") | OK | OK | OK |
| urllib.request | OK | OK | OK |
| ssl | OK | OK | OK |
| smtplib | OK | OK | OK |
| routes.helpers | OK | OK | OK |
| app / app object | OK | OK | OK |

固定日時 `2026-01-01T12:00:00+09:00` に対し、全3環境でkey `Asia/Tokyo`、
UTC offset `+09:00` / 32400秒を確認した。現在時刻には依存しない。
CPython 3.12.0はsystem timezone dataを使用し、tzdata未インストールのため
`tzdata_version` はnull。アプリ用環境への依存追加は行っていない。

全3環境で以下が一致した。

| 項目 | 結果 |
| --- | --- |
| app_imported | true |
| Blueprint | admin, api, history, line, match, participant |
| app.secret_key is None | true |
| _runtime_initialized | false |
| SQLite connect検出 | 0 |
| runtime config/state/DB file open検出 | 0 |
| socket connect検出 | 0 |
| initialize_runtime呼出し検出 | 0 |

hook対象の副作用を試みた場合は実行前に例外で遮断する。SQLAlchemy engineの
構成はimport時に行われるが、接続はしない。これは記載したhook対象の観測であり、
全I/Oを網羅する保証ではない。timezone package dataの読込は許可する。
sqlite3 import成功は永続SQLiteやSQL実行の利用可能性を意味しない。
urllib/ssl/smtplibのimport成功もLINE/SMTP通信の動作保証ではない。

安全な実測JSONは `results/cpython.json`、`results/local-worker.json`、
`results/workers-dev.json`。local Workerとworkers.devのJSONは完全一致した。
両環境でdiagnostics=200 / health=200、Set-Cookieなしをsmokeで確認。
今回、次のimport errorは発生しなかった。

## Cloudflareと測定値

- Worker: `shuttlers-match-import-poc`（既存Workerを更新）
- compatibility date: `2026-09-08` / flag: `python_workers`（変更なし）
- Worker Python: 3.14.2 / Pyodide: 314.0.6（両環境の応答で確認）
- CLI: uv 0.12.10 / workers-py (pywrangler) 1.17.2 / Wrangler 4.129.1
- SDK: workers-runtime-sdk 1.8.2 / CLI用CPython: 3.14.7
- アプリ検証用CPython: 3.12.0
- Local: `http://127.0.0.1:8788/cloudflare-import-poc/diagnostics`
  （検証後にローカルserverを停止）
- [Deploy diagnostics](https://shuttlers-match-import-poc.nowdon.workers.dev/cloudflare-import-poc/diagnostics)
- [Deploy health](https://shuttlers-match-import-poc.nowdon.workers.dev/cloudflare-import-poc/health)
- Deploy version: `1b9b6f61-0e7b-440e-ae54-78e11013adb4`

| 指標 | tzdata追加前 | 追加後 | 差分 |
| --- | ---: | ---: | ---: |
| dry-run additional modules | 543 | 1178 | +635 |
| Total Upload (KiB) | 10592.83 | 11164.64 | +571.81 |
| gzip bundle (KiB) | 2382.26 | 2520.68 | +138.42 |
| Worker Startup Time (ms) | 4113 | 4266 | +153 |

追加前のdry-runを今回再実行し、PR #70のmodule数・gzip値と一致した。
追加前startupはPR #70のdeploy記録（version
`b4eea080-1705-4713-ae88-ea70dc9e86df`）、追加後は今回deployのWrangler出力。
サイズ差分にはtimezone診断の追加コードも含む。startupは各1回の測定で、
app import失敗から成功への変化、probe順序の変更も含むため、tzdata単体の
性能コストとは断定しない。request応答時間やcold-start latencyの測定ではない。
性能最適化は行っていない。

既存認証でworkers.devのみにdeployした。binding追加なし。
本番domain/DNS/EC2や既存 `shuttlers-match-flask-poc` は変更していない。

```bash
uv run pywrangler deploy --dry-run
uv run pywrangler deploy
python smoke_http.py https://shuttlers-match-import-poc.nowdon.workers.dev
```

## 既存アプリとテスト

- baseline `pytest`: 315 passed (9.97s)。
- 関連 `pytest tests/test_cloudflare_import_poc.py`: 5 passed。
- 最終 `pytest`: 316 passed (9.98s)。
- timezoneの固定日時・offset、app importより先行するprobe順序を検証。
  既存の副作用遮断、公開例外情報の秘匿、source link境界テストも維持。
- HTTP smokeにtimezone version/offset、全Blueprint、runtime未初期化のassertを追加。
- `python -m compileall .`: exit 0。
- `git diff --check`: 成功。
- 変更ファイル: PoCのpyproject/pylock/uv.lock、diagnostics、smoke、README、
  resultsの3 JSON、および `tests/test_cloudflare_import_poc.py`。
- 本体app/models/routes/data/utils、requirements.txt、DB schema、route: 変更なし。
- config・両JSON state・既存history dump: baselineテスト後からのhash一致を確認。
- **既存pytestの副作用を検出**: baseline後から最終テスト後の間で
  `instance/participants.db` のhashが変化し、`instance/history_dumps/` に21 JSONが追加された。
  `tests/test_match_history.py`、`tests/test_line_notification_routes.py`、
  `tests/test_stats.py` のfixtureはchdirのみでapp.instance_path/DB URIを切り替えず、
  本体DBにdrop_all/create_allする。PoC診断のDB接続は0だが、全テストを含む
  作業全体について「DB変更なし」とは言えない。baseline前のDBは保存しておらず、
  開始時点の内容との比較・復元はできない。既存READMEの「隔離test DB」の記載を訂正する。
  DB/dumpはGit対象外でdiffに含まれない。復元や生成dumpの削除はしていない。
  今後は既存テストのDB/dump隔離を別タスクで修正する必要がある。
- 現行アプリへの手動request・UI確認: 未実施（今回の禁止事項に従う）。
- runtime初期化、SQL実行、外部通信、永続保存は未検証。
- commit / push / PR: 未実施。commit前にPoC専用lock、診断結果、symlink境界を確認。
- UI/操作仕様変更はないためmanual-site更新は不要。

## 次フェーズの候補（未実装）

1. runtime初期化と最小request実行、WSGI adapter、template/static同梱の独立PoC。
2. SQLAlchemyのengine構成と実際のDB接続・SQL実行を分け、永続DB方式を検証。
3. config / confirmed state / draft state / dumpの保存先と、confirm/revert/reset時の整合性を検証。
4. LINE・SMTPの通信方式を検証し、通知重複防止・部分失敗の既存動作を確認。

本体のtimezone遅延化、factory化、ORM置換、認証追加、D1/R2/KV/DO導入は行っていない。

## 公式資料

- [Python Workers / pywrangler](https://developers.cloudflare.com/workers/languages/python/)
- [パッケージ管理・自動bundle](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Wrangler python_modules設定](https://developers.cloudflare.com/workers/wrangler/configuration/)
- [Pyodide 314.0.6 zoneinfo制約](https://pyodide.org/en/stable/usage/wasm-constraints.html)
- [Pyodide 314.0.6 package一覧（tzdata 2025.3）](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
- [zoneinfoのdata sources](https://docs.python.org/3/library/zoneinfo.html#data-sources)
