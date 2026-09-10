# Cloudflare Jinja one-template-per-request PoC

2026-09-10、最新 `origin/develop` `18f7c05`（PR #73 merge）から
`codex/cloudflare-jinja-request-poc` を作成し、新規
`shuttlers-match-jinja-poc` のworkers.devだけで測定した。

結論は **A. 1 request = 1 templateなら安定**、かつ
**B. target templateのcache missは重いが、cache hit後は安定**。
公開676 requestは全成功し、1101/1102、例外、version混在は0だった。
この範囲では Cloudflare Python Workers + Flask/Jinja SSR の検討を継続可能。
ただし業務contextを必要とするtemplateのrenderや実routeは未検証である。

## 境界と構成

- 現行 `from app import app` と `app_module._flask_wsgi_app` を使用する。
- `app.wsgi_app` のruntime wrapperは書き換えず、外部dispatchだけで回避する。
- 1 requestは固定allowlist上の1 top-level templateに対し、load / compile /
  renderのいずれか1操作だけを行う。includeはtop-level renderに伴う通常処理。
- `env.cache.clear()` は実装にも計測にもない。Flask/Jinja標準cacheを維持する。
- renderは小さな合成contextで意味のある4 templateだけ。巨大なfake contextは作らない。
- 業務route、DB/config/state、runtime初期化、LINE/SMTP/network、Static Assetsは使わない。
- `SECRET_KEY`、CPU limit、D1/R2/KV/DO、custom domain、route、DNSは設定しない。
- Worker名は `shuttlers-match-jinja-poc`、公開先は
  `https://shuttlers-match-jinja-poc.nowdon.workers.dev` のみ。

公式資料上、Python WorkersはPyodide/CPythonをV8 isolate内で実行し、`pywrangler`
が依存をbundleしてWranglerへ処理を渡す。今回の実測はworkers.dev上のPoCであり、
workers.dev自体を本番配信先として推奨する主張ではない。

## Template分類

静的解析では14件すべてについてsource size、include、undeclared variablesを
`results/template-analysis.json` に保存した。Aは小さな合成contextでrender可能、
Cはsession/flash/config/state/DB/history dump等への依存が強いため今回render対象外。
B（flash等は使わないが、意味のあるrenderに大きな業務contextが必要）は現行14件には
該当なしと判断した。

| Template | load | compile | render | 分類 | render不可理由 |
| --- | --- | --- | --- | --- | --- |
| `_button_styles.html` | yes | yes | yes | A | — |
| `_flash_messages.html` | yes | yes | no | C | Flask flash/sessionを読む。SECRET_KEYは意図的に未設定 |
| `admin_settings.html` | yes | yes | no | C | configとflash/session partial |
| `index.html` | yes | yes | no | C | participant、match/config state、flash/session partial |
| `line_link_token.html` | yes | yes | yes | A | —（小さなparticipant/token context） |
| `match_edit.html` | yes | yes | no | C | draft/match/score helperとflash/session partial |
| `match_form.html` | yes | yes | no | C | flash/session partial |
| `match_history.html` | yes | yes | no | C | DB history/configとflash/session partial |
| `match_history_archives.html` | yes | yes | no | C | history dumpとflash/session partial |
| `match_result.html` | yes | yes | no | C | DB/match/config stateとflash/session partial |
| `participant_edit.html` | yes | yes | yes | A | —（小さなparticipant context） |
| `register.html` | yes | yes | no | C | Flask requestとflash/session partial |
| `thanks.html` | yes | yes | no | C | config/participant/LINE stateとflash/session partial |
| `upload_csv.html` | yes | yes | yes | A | —（includeと`url_for`を含む） |

## 測定列

local/publicとも同じ676 requestを直列実行した。

- 全14 templateのloadを各3回、compileを各3回: 84
- 代表4 templateのrenderを各100回: 400
  - 通常間隔79件、2秒間隔19件、15秒idle後1件、30秒idle後1件
- 全14 templateを1 requestずつ切り替えるload/compile: 28
- render可能4 templateの切替: 4
- 4 templateをround-robinするrender: 160

「first / second / warm」は各代表templateの測定列上の位置であり、cold startや
isolate identityを意味しない。`X-Jinja-Invocation` は全件1だったため、参考markerに
留める。cacheはrequest前後の `len(app.jinja_env.cache)` の増加でtarget missを判定した。
cache容量に達していないため、この測定内では増加をtarget compileとして扱える。

## 公開Worker実測

Wrangler 4.130.0、workers-py 1.17.2、workers-runtime-sdk 1.8.2。
version `038b20e8-59df-480e-bf15-0d201e8e1e1d`。tail coverage 676/676、
outcomeは全件`ok`。CPU/wallはms。少数4件のp95は補間値である。

| Template | mode | requests | success | 1101 | 1102 | CPU p50 | CPU p95 | CPU max |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `_button_styles.html` | load | 4 | 4 | 0 | 0 | 13 | 146.2 | 169 |
| `_button_styles.html` | compile | 4 | 4 | 0 | 0 | 19.5 | 26.25 | 27 |
| `_button_styles.html` | render | 141 | 141 | 0 | 0 | 6 | 9 | 192 |
| `_flash_messages.html` | load | 4 | 4 | 0 | 0 | 16 | 155 | 179 |
| `_flash_messages.html` | compile | 4 | 4 | 0 | 0 | 23.5 | 31.8 | 33 |
| `admin_settings.html` | load | 4 | 4 | 0 | 0 | 7 | 7.85 | 8 |
| `admin_settings.html` | compile | 4 | 4 | 0 | 0 | 65.5 | 105.45 | 111 |
| `index.html` | load | 4 | 4 | 0 | 0 | 8 | 8.85 | 9 |
| `index.html` | compile | 4 | 4 | 0 | 0 | 129 | 144.9 | 147 |
| `line_link_token.html` | load | 4 | 4 | 0 | 0 | 6 | 6 | 6 |
| `line_link_token.html` | compile | 4 | 4 | 0 | 0 | 26.5 | 33.1 | 34 |
| `line_link_token.html` | render | 141 | 141 | 0 | 0 | 6 | 10 | 227 |
| `match_edit.html` | load | 4 | 4 | 0 | 0 | 9 | 32.95 | 37 |
| `match_edit.html` | compile | 4 | 4 | 0 | 0 | 121.5 | 122.85 | 123 |
| `match_form.html` | load | 4 | 4 | 0 | 0 | 7 | 26.55 | 30 |
| `match_form.html` | compile | 4 | 4 | 0 | 0 | 15 | 18.4 | 19 |
| `match_history.html` | load | 4 | 4 | 0 | 0 | 7 | 8.85 | 9 |
| `match_history.html` | compile | 4 | 4 | 0 | 0 | 162.5 | 195.05 | 197 |
| `match_history_archives.html` | load | 4 | 4 | 0 | 0 | 7.5 | 29.25 | 33 |
| `match_history_archives.html` | compile | 4 | 4 | 0 | 0 | 104.5 | 114.4 | 115 |
| `match_result.html` | load | 4 | 4 | 0 | 0 | 13 | 157.65 | 183 |
| `match_result.html` | compile | 4 | 4 | 0 | 0 | 173.5 | 187.9 | 190 |
| `participant_edit.html` | load | 4 | 4 | 0 | 0 | 7 | 7.85 | 8 |
| `participant_edit.html` | compile | 4 | 4 | 0 | 0 | 33.5 | 35.85 | 36 |
| `participant_edit.html` | render | 141 | 141 | 0 | 0 | 5 | 9 | 52 |
| `register.html` | load | 4 | 4 | 0 | 0 | 6.5 | 7.85 | 8 |
| `register.html` | compile | 4 | 4 | 0 | 0 | 46.5 | 47.85 | 48 |
| `thanks.html` | load | 4 | 4 | 0 | 0 | 6 | 6 | 6 |
| `thanks.html` | compile | 4 | 4 | 0 | 0 | 41.5 | 48.95 | 50 |
| `upload_csv.html` | load | 4 | 4 | 0 | 0 | 6.5 | 7 | 7 |
| `upload_csv.html` | compile | 4 | 4 | 0 | 0 | 14 | 16.55 | 17 |
| `upload_csv.html` | render | 141 | 141 | 0 | 0 | 6 | 10 | 17 |

| Phase | requests | success | 1101 | 1102 | CPU p50/p95/max | wall p50/p95/max |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| all | 676 | 676 | 0 | 0 | 6 / 46 / 227 | 7 / 47.25 / 228 |
| representative repeat | 400 | 400 | 0 | 0 | 6 / 10.05 / 227 | 6 / 12 / 228 |
| 14-template load switch | 14 | 14 | 0 | 0 | 7 / 7.35 / 8 | 7 / 8 / 8 |
| 14-template compile switch | 14 | 14 | 0 | 0 | 42 / 186.1 / 190 | 43 / 188.1 / 192 |
| 4-template render switch | 4 | 4 | 0 | 0 | 6 / 6 / 6 | 6.5 / 7 / 7 |
| render round-robin | 160 | 160 | 0 | 0 | 5 / 8 / 25 | 6 / 8 / 26 |

## Cache、初回、idle

測定列のfirst/secondだけでは、別のcache状態を持つinvocationへ配分されるため、
常にmiss→hitにはならなかった。実際にcache件数が増えたrequestをmiss、増えなかった
requestをhitとして比較すると効果は明瞭だった。

| Template | target cache | requests | CPU p50/p95/max | wall p50/p95/max |
| --- | --- | ---: | --- | --- |
| `_button_styles.html` | miss | 1 | 192 / 192 / 192 | 193 / 193 / 193 |
| `_button_styles.html` | hit | 99 | 6 / 10 / 18 | 7 / 11.1 / 20 |
| `line_link_token.html` | miss | 5 | 27 / 188.8 / 227 | 28 / 189.8 / 228 |
| `line_link_token.html` | hit | 95 | 6 / 8 / 11 | 6 / 9.3 / 12 |
| `participant_edit.html` | miss | 5 | 34 / 50.8 / 52 | 34 / 52.8 / 54 |
| `participant_edit.html` | hit | 95 | 6 / 7.3 / 10 | 6 / 9 / 11 |
| `upload_csv.html` | miss | 4 | 16 / 16.85 / 17 | 16 / 17.7 / 18 |
| `upload_csv.html` | hit | 96 | 6 / 9 / 12 | 7 / 10.25 / 13 |

first/second/thirdの例では、`upload_csv.html` が16/16/6ms、
`line_link_token.html` が26/26/9ms、`participant_edit.html` が46/34/6ms。
`_button_styles.html` は7/192/7msで、2回目だけcache件数0→1のmissだった。
これをcold start/isolate変化とは断定しない。

idle後は4 templateすべて成功した。15秒idle後はCPU 5〜7ms、30秒idle後は5〜7ms。
2秒間隔76件も全成功（CPU p50/p95/max 6/9/52ms）。

## PR #73 Stage Hとの比較

Stage H結果は変更していない。Stage Hは1 request内で14 templateをload/parse/
referenced-template解析/get_templateし、最後にrenderした。H-cachedも同じ一括処理を
cache保持で行った。今回の14-template切替は、各requestが1 templateだけを扱う。

| Case | requests | success | 1101 | 1102 | CPU p50 | CPU p95 | CPU max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PR #73 H: 全template一括 | 50 | 7 | 39 | 4 | 0 | 1258.05 | 2213 |
| PR #73 H-cached: 全template一括/cache保持 | 50 | 8 | 32 | 10 | 2 | 2014.8* | 2035* |
| 今回: 14 requests load切替 | 14 | 14 | 0 | 0 | 7 | 7.35 | 8 |
| 今回: 14 requests compile切替 | 14 | 14 | 0 | 0 | 42 | 186.1 | 190 |
| 今回: render切替 | 4 | 4 | 0 | 0 | 6 | 6 | 6 |
| 今回: render round-robin | 160 | 160 | 0 | 0 | 5 | 8 | 25 |

`*` H-cachedは50件中5件がtail欠測のため45件のCPU集計。H/H-cachedの低い全件
p50は、CPU超過後の低CPU 1101/1102を含むため正常処理速度ではない。成功requestの
CPU中央値はH 1065ms、H-cached 957msだった。

## Local比較

同じ676 requestをlocal Workerへ実行し、676/676成功、1101/1102 0。
client elapsedはp50 21.2ms、p95 36.764ms、max 109.421ms。local Workerには
Cloudflare tailと同一のCPU/wall metricがないため、公開CPU値と同じ指標として比較しない。

## 副作用・bundle監査

成功した公開requestのimport/request guardは全件、以下が0だった。

- `sqlite3.connect`
- DB / DB sidecar / history dump open
- `config.json` / `match_state.json` / `draft_state.json` open
- `socket.connect`
- `initialize_runtime`

dry-run bundleは1195 files、Python filesystem static 0、forbidden runtime files 0、
credential match 0。uploadは1193 modules、11238.43 KiB / gzip 2535.04 KiB。
`instance/participants.db`、`instance/history_dumps/`、config/state、`.env`、
`.dev.vars`、SECRET_KEY、LINE/SMTP credential、カードPNG、CSVは含まれない。

## Test件数

PR #74のtracked filesだけを `git archive HEAD` で一時clean checkoutへ展開して
pytestを実行した結果は **330 collected / 330 passed**（collection errorなし）だった。
このcheckoutには `tests/test_cloudflare_jinja_request_poc.py` が含まれ、未追跡の
`tests/test_cloudflare_wsgi_poc.py` は含まれない。

作業treeでの結果は **333 passed**。この差分3件は、作業treeに残っている旧WSGI PoCの
未追跡テストを含むためであり、PR #74の正式なclean件数には含めない。

## 判定と次フェーズ

判定は **A + B**。単一templateでもcache miss時に最大227msのCPU spikeはあるが、
全件成功し、hit時とround-robinは低CPUで安定した。今回の範囲では
**Cloudflare Python Workers + Flask/Jinja SSRの検討を継続可能**。

次フェーズ候補は依頼どおり、D1実装ではなく
**使い捨てruntime data + SECRET_KEY + 最小DB依存request** の独立PoC。
今回render対象外とした業務context templateを、本番routeを直接呼ばず段階的に検証する。

## 再現

```bash
python cloudflare-jinja-request-poc/analyze_templates.py
python cloudflare-jinja-request-poc/build.py
uv run --directory cloudflare-jinja-request-poc/.build pywrangler dev --local --port 8788
python cloudflare-jinja-request-poc/measure.py local-worker http://127.0.0.1:8788
python cloudflare-jinja-request-poc/deploy.py
# 別terminalでtail後に公開計測し、raw tailは/tmpだけに保持
python cloudflare-jinja-request-poc/measure.py workers-dev https://shuttlers-match-jinja-poc.nowdon.workers.dev
```

詳細は `results/workers-dev-http.json`、`results/workers-dev-tail.json`、
`results/workers-dev-summary.json`、local対応ファイル、bundle/deploy metadataに保存した。

## 公式資料

- [Python Workers](https://developers.cloudflare.com/workers/languages/python/)
- [Python Workersの実行モデル](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [Python package / pywrangler](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Wrangler commands / tail](https://developers.cloudflare.com/workers/wrangler/commands/workers/)
- [workers.dev](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/)
