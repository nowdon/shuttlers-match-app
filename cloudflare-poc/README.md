# Cloudflare Flask PoC

DB非依存の Flask / WSGI / Blueprint / Jinja の起動確認用です。
本番 `app.py`、`models.py`、`data/`、設定・共有stateはimportも読み書きもしません。
session / flashを使わず、SECRET_KEYやLINE・SMTP等のsecretは不要です。

## 構成

- `src/worker.py`: `from workers import wsgi` と `Default = wsgi.entrypoint(app)`。
- `src/poc_app.py`: PoC専用Flask appとBlueprint。
- `src/templates/poc.html`: `render_template()` でファイルを読み込み、変数を展開。
  Wranglerが `templates/poc.html` をtext moduleとして自動同梱することを確認済み。
  既存のルート直下 `templates/` は使用しません。
- `pyproject.toml`: Flask 3.0.3（既存アプリと同じ）、開発用に
  workers-py 1.17.2 / workers-runtime-sdk 1.8.2。EC2用requirementsとは独立。
- `uv.lock`: CLI側の依存解決結果。
- `pylock.toml`: pywranglerが生成したWorker側の依存解決結果。
- `wrangler.jsonc`: compatibility date `2026-09-08`、flag `python_workers`。
  `workers_dev: true`。本番domainのrouteや永続ストレージbindingはありません。

Python WorkersはPyodide / WebAssembly上で実行されます。
今回CLIが選択したWorker用Pythonは **3.14.2**、Pyodide配布版は **314.0.6**。
これはローカルCLI起動用のCPython 3.13.2とは別です。
`requires-python >=3.13` はCLI側の要件で、Worker runtimeはcompatibility dateから決定されます。

## ローカル起動・確認

uvとNode.js / npm（npx）がPATHにある環境で、このディレクトリから実行します。
初回はCLIが必要なPython・パッケージ・Wranglerをダウンロードします。

```bash
uv run pywrangler dev --ip 127.0.0.1 --port 8787
```

別ターミナルで、このディレクトリから実行します。

```bash
python smoke_http.py http://127.0.0.1:8787
```

| Route | 期待する応答 |
| --- | --- |
| `/cloudflare-poc/health` | 200、status=ok / runtime=cloudflare-python-worker / framework=flask |
| `/cloudflare-poc/blueprint` | 200、status=ok / blueprint=cloudflare_poc |
| `/cloudflare-poc/template` | 200 HTML、shuttlers-match-app / Cloudflare Flask PoC |

スクリプトはHTTP 200、Content-Type、本文、Cookie非発行を検証します。
公開URLでは標準のPython-urllib User-Agentが403（Cloudflare 1010）となったため、
検証ツール自身を示す `shuttlers-match-flask-poc-smoke/0.1` を指定しています。
Cloudflare側のセキュリティ設定は変更していません。

## デプロイ

既存認証が利用可能な場合のみ実行します。認証がなければ停止し、
利用者が別途 `uv run pywrangler login` を行ってから再開してください。
既存Workerの上書きを避けるため、新規導入時は同名Workerが存在しないことを確認します。

```bash
uv run pywrangler whoami
uv run pywrangler versions list
uv run pywrangler deploy --dry-run
uv run pywrangler deploy
python smoke_http.py https://shuttlers-match-flask-poc.nowdon.workers.dev
```

`versions list` は新規導入時、Worker未作成ならcode 10007を返します。
既にこのPoCをデプロイ済みの場合はバージョンを返します。
本番domain `app.tby.aichi.jp`、DNS、EC2設定には割り当て・変更を行いません。

## 検証記録（2026-09-08）

- 使用CLI: uv 0.12.10、pywrangler（workers-py）1.17.2、Wrangler 4.129.1。
  pywranglerはnpxでWranglerを呼び出すため、今後の実行ではWrangler版も確認してください。
- Local: `http://127.0.0.1:8787`。上記3経路すべて200、本文一致、例外なし。
- Deploy: 既存OAuth認証を利用して新規PoC Workerを作成。
- 公開URL: <https://shuttlers-match-flask-poc.nowdon.workers.dev/cloudflare-poc/template>
- workers.devでも3経路すべて200、本文一致。標準User-Agentでの403は上記参照。
- dry-run: bindingなし、既存app・実データの同梱なし。gzipサイズ555.47 KiB。
- 既存アプリbaseline: `pytest` → 298 passed。
- 変更後: `pytest` → 301 passed（PoCの3テストを追加）。
- `python -m compileall .` → 成功（既存アプリ用CPython 3.12.0）。
- `git diff --check` → 成功。
- `tests/test_cloudflare_poc.py` は通常のCPythonでFlaskを確認するテスト。
  WorkerのWSGI adapterはmockせず、上記のlocal / deploy後HTTP試験で確認。
- 既存UIは未変更。参加者・管理者画面のブラウザでの手動確認は未実施。

## QR機能の整理

全体検索でQR生成routeへの利用導線は見つかりませんでした。
残っていたのはroute定義、appの再公開import、未使用import、route一覧テスト、依存宣言です。
`templates/thanks.html` はPayPay URLへの直接リンクでした。
外部クライアントや過去アクセスログの利用状況は、このリポジトリ検索では確認できません。

- `routes/participant.py`: `/qrcode/<user_type>` と `qrcode_image()`、専用importを削除。
- `app.py`: `qrcode` と `qrcode_image` のimportだけ削除。
- `routes/helpers.py`: 未使用の `qrcode` importを削除。
- `requirements.txt`: `qrcode[pil]==7.4.2` を削除。他の既存依存は変更なし。
- `tests/test_route_registration.py`: 削除したrouteのみ期待値から除外。

QR route以外の既存route、UI、支払い導線、models、DB、共有state、通知、dumpは未変更です。
利用手順が変わらないためmanual-siteも変更していません。
commit前にはQR削除の範囲とPoC専用設定・lockfileを確認してください。

## 次フェーズの障壁（今回は未対応）

1. `app.py` はimport時にSECRET_KEYを要求し、SQLAlchemyを初期化して
   `db.create_all()` とSQLite schema互換処理を実行します。
   Flask単体の成功はこれらの実行可否を保証しません。
2. Flask-SQLAlchemy / SQLAlchemyとDB driverのWorker上でのimport・実行互換性、
   DB session・transaction・既存履歴の移行方法を別途検証する必要があります。
3. `instance/participants.db`、JSON設定・confirmed / draft state、history dumpは
   ローカルファイルを前提としています。Workerのfilesystemは一時的でisolate間共有もないため、
   永続化とconfirm / revert / resetの整合性を別途設計する必要があります。
4. `routes/helpers.py` などのimport時設定読み込み、ZoneInfoデータ、既存template群、
   static画像・CSVの同梱・配信方法を確認する必要があります。
5. LINEの `urllib.request.urlopen` とSMTPの `smtplib` は、Worker上の通信・認証情報の
   受け渡し・タイムアウトを実証する必要があります。既存の重複防止・失敗時仕様も検証対象です。

## 参照した公式資料

- [Flask / WSGI構成](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
- [Python WorkersのパッケージとCLI](https://developers.cloudflare.com/workers/languages/python/packages/)
- [runtimeの仕組み](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [標準ライブラリと一時filesystem](https://developers.cloudflare.com/workers/languages/python/stdlib/)
- [Wrangler設定](https://developers.cloudflare.com/workers/wrangler/configuration/)
