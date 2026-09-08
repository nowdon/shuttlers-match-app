# Cloudflare Python Worker stability PoC

2026-09-08、`origin/develop` `52e964b`（PR #72）から
`codex/cloudflare-worker-stability-poc` を作成。既存3 PoCと既存Workerは変更せず、
新規 `shuttlers-match-stability-poc` のworkers.devだけを使って段階比較する。
本体Python・templates・staticの内容、CPU limit、runtime behavior、永続データは変更しない。

## 測定方法

各stageを独立した `.build/<stage>/` に生成する。A/B/C以降は依存profileが別で、
AにFlask等が紛れ込まない。全stageを同じWorkerへ**順番に**deployし、
そのstageの測定が終わるまで次のdeployをしない。版の混在は応答stage headerと
tailのversion IDで検出する。旧版が含まれたrunは `*-rollout-*.json` に別保存し、
30秒後に再測定する。失敗を成功するまでretryして隠す方式ではない。

各operationを50回、curlで直列に測定。旧版に到達したrequestはversion IDで除外し、
新endpointのFでは不足分21回を追加した（`top_up.py`）。表は版が一致したrequest数を示す。各stageの最初のoperationは:

- 40回: 0.1秒間隔（request完了後からの間隔）
- 9回: 2秒間隔
- 最後の1回: 15秒空けた後

追加operationは50回ずつ0.1秒間隔。client latencyはcurl process開始から終了までを
測るため、HTTP転送以外のclient起動時間も含む。CPU/wallはtailで得た別の指標。
公開requestのIP・location・full headers・response bodyは測定結果に保存しない。
Ray ID、probe ID、応答bodyのsize/hash、許可した診断headerのみを保存する。
raw tailは `/tmp/stability-tail.log` のみで、Git対象外。

D以降のraw応答にある `invocation_marker` はPython entrypoint object内の連番。
これはinstance再利用の参考情報であり、cold-startやisolate IDの証明ではない。
初回requestや15秒idle後をcoldと断定せず、観測した間隔との相関として扱う。
A/B/Cは最小性を保つためこのmarkerを入れていない。

## 構成

| Stage | 内容 | operation |
| --- | --- | --- |
| A | workers SDKだけ、追加dependencyなし | raw固定応答 |
| B | Flask importのみ | raw固定応答 |
| C | Flask / Flask-SQLAlchemy / SQLAlchemy / tzdata / models import | raw固定応答 |
| D-off | 現行app import、guard module自体を含めない | raw固定応答 |
| D-on | 現行app import、既存guard ON | raw固定応答 |
| E-off | 現行app + 元WSGI callable、guard OFF | raw / WSGI固定応答 |
| E-on | 同上、guard ON | raw / WSGI固定応答 |
| F | 元の36ルートを列挙 | URL map |
| G | 最小templateからincludeまで分離 | load / compile / render / include |
| H | Python filesystemにもAssetsにもstaticなし | raw / WSGI / map / template各operation / full |
| I | H + Static AssetsだけにCSV/PNG | raw / Assets CSV / Assets PNG / full |
| J | I + Python filesystemにもCSV/PNG | raw / Flask CSV / Flask PNG / full / 負荷後raw |
| H-off | Hの固定入力full compile対照、guard OFF | raw / full |
| H-cached | guard ON、既存PoCと同じキャッシュ維持・現行14 templates | raw / full |

G以降は15 templates（現行14個+PoC最小1個）を同梱するが、Gの各operationは
指定したtemplateしか読み込まない。`probe.html` は短い合成templateで、
loadはloader.get_sourceのみ、compileはJinja.compileまで、renderはfrom_string/render。
includeは現行upload_csv.htmlと_button_styles.htmlを解決する。fullでは
現行14個と最小1個をload/parse/compileしてupload_csv.htmlをrenderする。
H-cached以外のinclude/fullは各requestでcacheをクリアし、再compileの負荷を明示的に測る。
H-cachedのfullは既存PoCと同じくcacheを保持し、現行14 templatesだけを毎回load/parseし、
get_templateとupload_csvのrenderを行う（応答のJSON包装だけは省く）。
これ以外のDB/config/state依存viewは呼ばない。

現行app objectと `_flask_wsgi_app` を使い、`app.wsgi_app` は書き換えない。
PoC専用viewだけを追加し、Workerのpath allowlistから元WSGI callableを呼ぶ。
GET以外を拒否し、業務viewを外部dispatchしない。

A/B/Cの固定応答も含めて全buildが `workers-runtime-sdk==1.8.2` を同梱する。
これはPython Workersの必要なSDKであり、AにはFlask/Pyodideの追加packageはない。
CLIはworkers-py 1.17.2、測定時Wranglerは各deployログで確認する。
`profiles/` にminimal / Flask / app用のpyproject・pylock・uv.lockを保存。
アプリdependencyはFlask 3.0.3、Flask-SQLAlchemy 3.1.1、tzdata 2025.3を固定。

## guardと副作用

`src/side_effect_guard.py` は既存WSGI PoCのguardの**同一bytesの複製**。
旧PoCを未コミットのまま保持でき、今回のPoCも単独で再現できるようにした。
worker本体・models・routes/data/utils・templatesは本体への相対symlinkで参照する。

D-on以降のON構成はimport前と同期request実行中にaudit/profile guardを有効にする。
WSGI iteratorの消費とcloseもguard内に含める。OFF構成はguard moduleのbundle/importを
行わず、同じ安全な固定pathを使う。H-offの追加対照も固定入力のテンプレート処理だけで、
本体のDB/config/state依存viewは呼ばない。guard OFFのDB/configカウンタはnull（未観測）であり、
0とは報告しない。raw viewはruntime未初期化とsecret未設定をassertする。
CPython回帰テストでは別のaudit hookでDB接続・config/state/DB openを禁止して検証する。

各deploy前に `audit_bundle.py` を実行し、許可したソースとpackage以外の混入、
DB/config/state/history dump/.env/.dev.vars、環境credentialの混入を検査する。
H以前のstatic countは0、IはAssetsに55個だけ、JはPython filesystemにも55個。
Static Assetsの対象も既存CSV1個とカードPNG54個の明示的なsymlinkだけに絞る。
実データのhash一覧やcredential値は結果へ保存しない。

## 再現

uv / Node.js / npmをPATHに用意し、repository rootから実行する。
既存カードPNGは元の `static/cards/` に存在する必要がある（PNGはGit対象外）。
`.build/` は生成物で、過去の結果を上書きしないためbuilderは既存directoryを拒否する。

```bash
python cloudflare-stability-poc/build_stage.py A
uv run --directory cloudflare-stability-poc/.build/A pywrangler sync
python cloudflare-stability-poc/run_stage.py A
```

stageを順番に指定可能:

```bash
python cloudflare-stability-poc/run_stage.py B C D-off D-on E-off E-on F G H I J
# 別プロセスで、F/Gの旧版応答の不足分を補う
python cloudflare-stability-poc/top_up.py
python cloudflare-stability-poc/collect_tail.py /tmp/stability-tail.log cloudflare-stability-poc/results/tail-sanitized.json
python cloudflare-stability-poc/summarize.py
```

実行前に各stageをbuild/syncする。tailは別terminalで、**新規stability Workerだけ**に接続する。
各stageの送信内容とhashは `*-manifest.json`、audit結果は `*-audit.json`、
bundle/startup/versionは `*-deploy.json`、request結果はstage-operation別JSONに記録する。
`http-tail-joined.json` はRay IDで結合し、probe IDとdeploy versionを再照合する。

## 結果

測定期間: 2026-09-08〜09（JST）。判定は **E（全テンプレートのJinja処理）を起点に、I（公開Python/Pyodide runtimeのCPU超過後の挙動）の可能性**。単純なimport、WSGI、static、guard単独では再現しなかった。

| Stage | 内容 | bundle gzip KiB | requests | success | 1101 | 1102 | その他 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 最小SDK固定応答 | 27.26 | 50 | 50 | 0 | 0 | 0 |
| B | Flask import | 555.03 | 50 | 50 | 0 | 0 | 0 |
| C | 現行dependency import | 2490.72 | 50 | 50 | 0 | 0 | 0 |
| D-off | app import / guard OFF | 2520.55 | 50 | 50 | 0 | 0 | 0 |
| D-on | app import / guard ON | 2521.01 | 50 | 50 | 0 | 0 | 0 |
| E-off | WSGI固定 / guard OFF | 2520.55 | 100 | 100 | 0 | 0 | 0 |
| E-on | WSGI固定 / guard ON | 2521.01 | 100 | 100 | 0 | 0 | 0 |
| F | 36 URL map | 2521.01 | 50 | 50 | 0 | 0 | 0 |
| G | 最小template / include | 2534.45 | 200 | 200 | 0 | 0 | 0 |
| H | staticなし / full追加 | 2534.45 | 400 | 357 | 39 | 4 | 0 |
| I | Static Assetsのみ | 2534.45 | 196 | 149 | 43 | 4 | 0 |
| J | Python filesystem static追加 | 7872.64 | 250 | 151 | 95 | 4 | 0 |
| H-off | full / guard OFF | 2533.55 | 100 | 62 | 33 | 5 | 0 |
| H-cached | 現行14 templates / cache維持 | 2534.49 | 100 | 58 | 32 | 10 | 0 |

Fは旧versionの404を21件除外し、21件補充した。Iは旧H versionの4件を除外した。その他のrollout測定は別保存。Jは負荷後raw 50件も含む。H-cachedはtail接続断により8件（raw 200が3件、full 200が2件、full 1102が3件）のCPU/wall/versionが欠測。HTTP結果は全100件を保存し、欠測を0msや成功へ置換していない。

### 1101と最小再現

Hでは先行350件（raw、固定WSGI、URL map、load、compile、render、include）が全成功。全テンプレートを扱う `/template/full` の50件で7成功 / 39件1101 / 4件1102になった。staticはPython filesystemにもAssetsにも0個。Gの最小templateおよびincludeは各50件成功した。

最初の1101はH fullの0起算seq 3。tailのPythonErrorに `introspection.CpuLimitExceeded: Python Worker exceeded CPU time limit` が含まれる。呼出し経路は `workers/wsgi.py:fetch/process_request → worker.py:safe_wsgi → Flask dispatch → template_probe → jinja2 environment/compiler → side_effect_guard.py:profile → introspection.py:raise_cpu_limit_exceeded`。詳細のbasename・行・functionは `results/http-tail-joined.json` に保存した。

最初のErrnoErrorはH full seq 7、Ray `a37eb8bebbe1fc98`、500/1101、outcome `exception`、CPU 2ms / wall 2ms。messageは `#<Object>` だけでnative stackがなく、**ErrnoError自体の発生module/functionは特定不能**。PythonErrorには別途 `SystemError: Cannot enter a promising task from inside another running promising task. This is a bug in Pyodide.` も観測された。CPU超過とその後のruntime失敗は時系列で観測したが、全ErrnoErrorの内部原因を断定する根拠はない。

Jではfull以前のraw・Flask CSV・Flask PNGは各50/50成功。full後のrawは50/50が1101（多くはCPU 0〜2ms）で、15秒idle後も失敗した。最初から固定Responseで不安定だったのではない。true isolate IDを取得していないため、同じisolateの破損やcold-startを証明したとは扱わない。deploy直後の旧version混在は別問題として除外した。

guard OFFのH-offでもfull 50件が12成功 / 33件1101 / 5件1102。guardは必要条件ではなく、D/EのON/OFF固定応答はいずれも全成功。guardによるCPU負荷の差はあり得るが、この異なる公開invocation群から影響量は断定しない。

追加H-cachedはcacheを消去せず現行14 templatesを扱う既存PoCと同じload/parse/get_template/render手順。raw 50/50成功、fullは8成功 / 32件1101 / 10件1102。再現に毎回のcache消去も必要ない。JSON包装・static診断等を省いた最小対照であり、旧PoC全体とbyte単位で同じ処理ではない。

### 1102

最初はH full seq 4、CPU 2213ms / wall 2499ms、tail outcome `exceededCpu`、例外 `Worker exceeded CPU time limit.`。取得できた1102 tailはすべて `exceededCpu` で、memory-related outcomeは観測されなかった。H-cachedの3件はtail欠測でCPU/memory分類不能。測定値が小さい後続1102も省かず記録する。これらを設定されたCPU上限そのものの測定値とは扱わない。`limits.cpu_ms` は一切設定・変更していない。

| Stage | 1102のCPU/wall（ms、各request） | static Python / Assets |
| --- | --- | --- |
| H | 2213/2499, 15/17, 52/55, 24/25 | 0 / 0 |
| I | 2050/2169, 13/15, 19/20, 17/19 | 0 / 55 |
| J | 1571/1660, 1624/1801, 1564/1671, 20/23 | 55 / 55 |
| H-off | 519/532, 10/14, 303/311, 355/361, 27/28 | 0 / 0 |
| H-cached | 欠測, 欠測, 欠測, 2020/2153, 1994/2275, 2029/2152, 24/26, 14/16, 10/12, 2035/2228 | 0 / 0 |

全件が `/template/full`。parse/referenced-template解析/get_template compileと最終renderを含むoperationだが、最初の有用なPython stackはJinja compiler内を指している。単純renderやinclude単独で同じ障害は観測しなかった。各requestをcold/warmへ分類できる証拠はない。

PNG/CSVをPython bundleから外すとgzipは7872.64 → 2534.45 KiB（約68%減）。それでもH/Iで再現したため、bundle削減だけではこの障害を回避できない。IのAssets CSV/PNGは各50/50成功。I/Jの通常deploy設定は `run_worker_first: true` で、Workerから明示的にASSETS bindingを呼ぶ。今回asset-first比較は追加していない。

### bundle / startup

| Stage | modules | uncompressed KiB | gzip KiB | Assets個数 | deploy startup ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 28 | 99.01 | 27.26 | 0 | 1120 |
| B | 221 | 2193.88 | 555.03 | 0 | 2134 |
| C | 1152 | 11032.58 | 2490.72 | 0 | 5113 |
| D-off | 1177 | 11164.23 | 2520.55 | 0 | 5057 |
| D-on | 1178 | 11165.75 | 2521.01 | 0 | 5142 |
| E-off | 1177 | 11164.23 | 2520.55 | 0 | 4572 |
| E-on | 1178 | 11165.75 | 2521.01 | 0 | 5691 |
| F | 1178 | 11165.75 | 2521.01 | 0 | 6050 |
| G | 1193 | 11235.49 | 2534.45 | 0 | 5537 |
| H | 1193 | 11235.49 | 2534.45 | 0 | 5777 |
| I | 1193 | 11235.49 | 2534.45 | 55 | 5005 |
| J | 1248 | 16594.76 | 7872.64 | 55 | 6804 |
| H-off | 1192 | 11233.97 | 2533.55 | 0 | 4557 |
| H-cached | 1193 | 11235.68 | 2534.49 | 0 | 5061 |

startupはdeployログの値。curl latencyやtail CPU/wallとは別指標。request別値は各operation JSON、path別CPU/wall集計は `results/summary.json`。低CPUの失敗を正常処理の速度と混同しないよう、成功requestだけのCPU中央値も保存した。CLIはworkers-py 1.17.2 / Wrangler 4.129.1。今回Aの最小性を保つため公開Python/Pyodide version endpointは追加していない。syncログ上のPythonは3.14.2で、package/runtimeをstage間で変更していない。

### 検証・安全性・残る課題

- CPython: PR #73のtracked filesだけを `git archive HEAD` で一時ディレクトリへ展開し、同じPython環境で `pytest`: **324 passed**（collection errorなし）。以前の327件は未追跡の旧WSGI PoCテスト3件を含む値だったため訂正。guard ON/OFF/cache対照の固定WSGIとtemplateに、実データopen・DB/network接続を独立audit hookで禁止するテストを含む。
- local Worker J: raw 50/50、full 50/50成功。公開と同じfull負荷で1101/1102は再現しなかった。業務UIの変更はなく、ブラウザUI比較は今回行っていない。
- `python -m compileall .` と `git diff --check` はexit 0。新規未追跡ファイルの空白も別途確認した。
- pytest前後の実DB・config・state・dump計842項目はhash/count完全一致。既存PoC内容171ファイルも一致。本体・template・static・requirementsのdiffなし。
- 全deployのdry-run bundleは実DB/config/state/dump/.env/.dev.vars混入0、環境credential一致0。診断は固定pathだけで実データ依存viewへdispatchしない。観測できたguard ON import/request countersは全0。OFFと失敗時の未取得countersは未観測であり0とは主張しない。
- 結果JSONにIP/location/full headers/secret値・実データ内容を保存していない。raw tailはGit対象外の/tmpだけ。送信対象は許可されたソース・template・CSV/PNG・PoC/packageだけ。
- 新規Workerのworkers.devのみ更新。旧Worker、app.tby.aichi.jp、DNS、EC2、route/custom domain、D1/R2/KV/DO、本体runtime behaviorは変更なし。commit / push / PR未実施。
- 原因分類はEが最も強く、CPU超過後の低CPU ErrnoError/Pyodide例外とlocalとの差からIも疑う。WSGI固定応答は安定したが、重いJinjaをWSGIなしで実行する対照は未測定のため、WSGIとの相互作用まで排除しない。
- ErrnoErrorのnative stack、true isolate identity、Cloudflare内部のCPU accounting/recoveryは未取得。修正・runtime変更・CPU引上げによる解決は今回の対象外。これらが次の調査候補。
- 最終公開versionはH-cached `1729e1c4-e9ca-499b-af5d-b492524e0b11`。障害再現用の状態を残している。測定用local/tailプロセスは終了済み。

公開先: https://shuttlers-match-stability-poc.nowdon.workers.dev

変更ファイルはこのPoCのbuilder、Worker、guard複製、測定/audit/集計スクリプト、固定依存profilesとlockfile、README、sanitized results、および `tests/test_cloudflare_stability_poc.py`。commit前にはこれらの新規ファイルを確認し、既存未追跡のWSGI PoC/testと混在させない。

## 公式資料

- [1102はCPUまたはmemory limit](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1102/)
- [Tailのoutcome・CPU/wall](https://developers.cloudflare.com/workers/runtime-apis/handlers/tail/)
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Python bundled file読込](https://developers.cloudflare.com/workers/languages/python/examples/)
- [Python Workersのsnapshotと実行](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
