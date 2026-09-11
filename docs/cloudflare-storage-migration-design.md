# Cloudflare D1 / R2 storage migration design

作成日: 2026-09-11

基準: `origin/develop` / `2a1ea445f68e94b0e875a31782ce0f8c14fdf9a4`（PR #75 merge commit）

対象: Flask application (`app.py`, `models.py`, `logic.py`, `data/`, `routes/`, `utils/`)

非対象: `manual-site/`、過去の `cloudflare-*-poc/`、tests、今回開始時点で未追跡だった `cloudflare-wsgi-poc/` と `tests/test_cloudflare_wsgi_poc.py`

この文書は設計のみである。D1 database、R2 bucket、migration file、application code は作成・変更しない。

## 1. Executive summary

現行の永続化先は、relational data の `instance/participants.db`、確定・下書き状態の `match_state.json` / `draft_state.json`、管理画面設定の `config.json`、履歴バックアップの `instance/history_dumps/*.json` の4系統である。Cloudflare Python Workers の filesystem は isolate ごとの一時領域であり、永続化には使えない。PR #75 のPoCも、現行 Flask / SQLAlchemy / raw `sqlite3` がWorker上で動作する一方、requestを跨ぐSQLite永続性は保証されないことを実測している。Cloudflareの公式資料も、Python Workerのfilesystemはephemeralかつisolate間非共有としている。[Python Workers standard library](https://developers.cloudflare.com/workers/languages/python/stdlib/)

推奨配置は次のとおり。

| 現行 | 移行先 | 方針 |
| --- | --- | --- |
| 10 relational tables | D1 | 既存の意味とIDを維持し、prepared statement と明示的 transaction command へ移す |
| `match_state.json` | D1 `runtime_state` の single-row JSON | relational更新と同じ `batch()` に含められる形にする |
| `draft_state.json` | D1 `runtime_state` の single-row JSON | `version` を持たせ、編集競合を検出する |
| `history_dumps/*.json` | private R2 bucket | filename互換を維持した年月prefix、JSON bytesを直接 put/get |
| admin-editable `config.json` | D1 `app_config` single-row JSON | 設定全体をversion付きでatomic更新 |
| credential | Worker Secrets | D1やvarsへ保存しない |
| deploy/environment値 | Worker vars | non-secretかつ管理画面から編集しない値だけ |

最重要の実装境界は「同期Flask routeからasync D1 bindingを呼ぶ方法」である。Cloudflareの現行Flask資料は、bindingを `request.environ["workers.env"]` から取得し、`pyodide.ffi.run_sync` で非同期binding操作を同期handlerへ橋渡しする例を公式に示している。[Flask on Python Workers](https://developers.cloudflare.com/workers/languages/python/packages/flask/) したがって第一候補は、requestごとにD1/R2 bindingを受け取る小さなadapterを `flask.g` に置き、そのadapter内部だけで `run_sync` を使う方式（比較案D）である。ただし公式例はAssets bindingであり、D1固有の例外・result proxy・`batch()`・繰り返し呼び出しについては **要PoC** とする。

現在の `data/` はread boundaryとして有用だが、write、ORM attribute mutation、commit、bulk delete、state file操作がroutes/helpers/utilsへ残る。generic repository/service/DTOを全面導入せず、既存のnamed queryを維持しながら、複数table/stateを同時に扱う操作だけを明示的なcommandへ寄せる。

全面的なDurable Object採用は推奨しない。participant registration、notification reservation、単純設定更新はD1のunique constraint / conditional update / batchで足りる。confirm・revert・reset・draft editを同一eventに対して完全に直列化したい場合だけ、D1 PoCでoptimistic concurrencyとbatchの限界が確認された後に「1 event/session = 1 Durable Object」を検討する。

## 2. Current persistence inventory

### 2.1 検索方法と件数定義

最初にrepository全体（tests、manual site、PoCを含む）へ依頼記載のpatternを `rg` で検索し、その後production Pythonを上記対象へ限定した。`open()` はnetworkの `urlopen()`、upload stream、単なるdocumentation例を除外した。

production relational accessは、`Model.query`、`db.session.<method>`、`db.create_all`、`inspect(db.engine)`、`sqlite3.connect` の**構文上のcall site**を数える。method chainは `.query` 1箇所として数え、relationship lazy loadは見えないため別記する。

| 集計 | 件数 | 内訳 |
| --- | ---: | --- |
| production DB access call sites | 88 | `Model.query` 37、`db.session.*` 47（`query` 1、`get` 2を含む）、`db.create_all` 2、`inspect(db.engine)` 1、raw `sqlite3.connect` 1 |
| raw sqlite3 | 1 | `utils/db_utils.py:get_all_participants` |
| state API call sites | 44 | confirmed state 24、draft state 20。定義行を除く。実filesystem I/O primitiveは5箇所 |
| history dump filesystem operations | 10 | 8関数内の `isfile` 1、`stat` 1、`open` 4、`isdir` 1、`listdir` 1、`makedirs` 1、`read_bytes` 1。path組み立てとhigh-level callerは除く |
| config filesystem I/O | 2 | `load_raw_config` のread、`save_config` のwrite |

`Participant.line_account`、`MatchRound.matches`、`MatchRound.bench_players`、`LineLinkToken.participant/session` 等のrelationship accessは追加queryを発行し得るが、上記88件に含めない。D1では暗黙lazy loadを廃止し、data関数内のJOINまたは明示的な複数readへ置き換える。

### 2.2 Production access table

| File / function | Data | R/W | Current backend | Transaction requirement | Proposed backend |
| --- | --- | --- | --- | --- | --- |
| `app.ensure_database_tables` | all relational schema | W/DDL | Flask-SQLAlchemy / SQLite | startup migrationとは分離 | D1 migration files（request時 `create_all` は廃止） |
| `app.ensure_match_history_score_text_column` | `match_histories.score_text` | R/DDL | inspector + raw ALTER | standalone schema compatibility | D1 versioned migration |
| `data/participants.py` 5 reads | Participant | R | ORM | read-only | D1 prepared SELECT via named data functions |
| `data/match_history.py` 6 reads | MatchRound/History/Bench | R | ORM + selectinload | read-only; stable ordering required | D1 SELECT/JOIN; orderingをSQLで明示 |
| `data/match_sessions.py:get_match_session_by_id` | MatchSession | R | ORM session.get | read-only | D1 SELECT by PK |
| `data/line_notifications.py` 8 reads | LINE tables + Participant | R | ORM/JOIN/selectinload | duplicate check alone is not a lock | D1 prepared SELECT/JOIN; reservationはwrite command化 |
| `logic.get_consecutive_player_ids` | recent rounds + matches; match state/config | R | ORM lazy relationship + JSON files | mutually consistent snapshot preferred | D1 read command/JOIN + runtime/config rows |
| `utils.pair_optimizer.get_historical_pair_counts` | MatchHistory | R | ORM | read-only | data-layer aggregate SELECT |
| `utils.stats.calculate_participant_win_stats` | Participant/MatchHistory | R | ORM | read-only | data-layer aggregate/read |
| `utils.db_utils.get_all_participants` | selected Participant columns | R | raw sqlite3 | independent connection; no caller transaction | remove raw path; D1 participant read preserving `active` 0/1 API shape |
| `routes.participant.register` | Participant | W | ORM | one insert; card UNIQUE is final arbiter | D1 insert command, constraint→HTTP 400/409 mapping |
| `routes.participant.participant_view` | Participant fields/active | W | ORM dirty tracking | one update | D1 explicit update; keep current behavior (weight is not recalculated) |
| `routes.admin.upload_csv` | Participant × rows | W | ORM single commit | all accepted rows atomic; card UNIQUE | prevalidate, D1 batch inserts |
| `routes.admin.reset_db` + `clear_all_data_records` | all 10 tables | W/Delete | ORM bulk delete | all relational deletes atomic; dump/state/mail outside | D1 batch in FK-safe order; R2/state boundaries explicit |
| `routes.history` single/round score updates | MatchHistory score/winner | W | ORM dirty tracking | one match or whole round atomic | D1 single update / batch |
| `routes.history.dump_and_clear` | history tables + dump | R/W/Delete | ORM + filesystem | dump best effort; three DB deletes atomic | D1 reads + R2 put outside delete batch; semantics preserved |
| `routes.match.confirm_match` | round, matches, bench, games, session, confirmed/draft state | R/W | ORM + two JSON files | relational set must be atomic; state currently non-atomic | D1 transaction command/batch; notification after commit |
| `routes.match.revert_match_to_draft` | same subset in reverse | R/W/Delete | ORM + two JSON files | relational rollback + states should be atomic | D1 transaction command/batch |
| `utils.reset.reset_match_state` | session, Participant counts, both states | W | multiple ORM commits + files | target design: one atomic logical transition | D1 transaction command; current sequencing documented below |
| `utils.match_session.ensure/close` | MatchSession + match state | R/W | ORM commit + JSON | currently split; target atomic with current-state row | D1 session/state command |
| `routes.line.start/unsubscribe` | Subscription/Token | R/W | ORM | each route commit atomic; unique constraints authoritative | D1 command/batch |
| `routes.helpers.complete_line_link` | Token, Account, Subscription | R/W | ORM single commit | account/subscription/token-use atomic | D1 batch with token unused/unexpired guard; race handling required |
| `routes.helpers.send_match_confirmed_line_notifications` | Notification + DeliveryLog | R/W + network | two DB commits + LINE HTTP | reservation/log setup atomic; network best effort; finalization atomic | D1 reservation/finalization batches; LINE outside transaction |
| `routes.helpers.clear_match_history_records` | Round/History/Bench | Delete | ORM bulk delete | three deletes atomic at caller commit | D1 batch |
| `utils.match_state` + callers | confirmed runtime state | R/W | `match_state.json` | desired with relational transition | D1 `runtime_state/current_match` JSON row |
| `utils.draft_state` + callers | editable draft/fixed pairs | R/W/Delete | `draft_state.json` | per edit atomic; confirm coordination needed | D1 `runtime_state/current_draft` JSON row + version |
| `utils.config`, admin settings and consumers | application settings | R/W | `config.json` | whole document atomic desired; current file write is not atomic replace | D1 `app_config/main` JSON row + version |
| history archive helpers | dump JSON archives | R/W/List | instance filesystem | R2 operation; not in D1 transaction | private R2 |
| `utils.mail_sender._build_message` | dump attachment bytes | R | `Path.read_bytes` | email is external/best effort | R2 bytes passed directly to mail adapter |

### 2.3 State schemas from code

`match_state.json` default has four keys. Writers may add three more:

```json
{
  "match_active": false,
  "match_count": 0,
  "matches": [[1, 2, 3, 4]],
  "bench": [5],
  "timestamp": "2026-09-11T12:34:56.789012+09:00",
  "court_count": 1,
  "session_id": 10
}
```

- required by readers: `match_active` boolean-like、`match_count` integer-like、`matches` list of participant-ID lists、`bench` participant-ID list。
- optional/legacy-compatible: `timestamp`, positive integer `court_count`, integer `session_id`。
- `save_match_state_full` preserves existing `session_id` unless explicitly overridden, but reconstructs the other keys.
- timestamps use local aware ISO-8601 (`datetime.now().astimezone()`), whereas relational timestamps are created as UTC datetimes.

`draft_state.json` active shape is:

```json
{
  "draft": true,
  "timestamp": "2026-09-11T12:34:56.789012+09:00",
  "matches": [[1, 2, 3, 4]],
  "bench": [5],
  "court_count": 1,
  "fixed_pairs": [[1, 2]]
}
```

`court_count` and `fixed_pairs` are optional. Active判定は `draft is True` かつ `matches` / `bench` がlistであることだけを要求する。現行互換処理は最後の4人未満groupをlegacy benchとして扱う場合がある。`fixed_pairs` は現在のdraftだけに属し、confirmの `clear_draft_state` と次の新規生成で引き継がない。

## 3. Current relational schema

### 3.1 Columns

`default` はSQLAlchemy Python-side defaultであり、現行SQLite DDLにはserver defaultとして出ない。`onupdate` もORM-sideである。`String(n)` の長さはSQLite自体では強制されない。

| Table.column | SQLAlchemy type | NULL | Default/on update | Key/unique/FK |
| --- | --- | --- | --- | --- |
| `participants.id` | Integer | no (PK) | auto rowid | PK |
| `.name` | String(80) | no | — | — |
| `.gender` | String(10) | no | — | — |
| `.level` | String(20) | no | — | — |
| `.weight` | Float | no | — | — |
| `.games_played` | Integer | yes | Python `0` | — |
| `.active` | Boolean | yes | Python `True` | — |
| `.card` | String(10) | no | — | UNIQUE |
| `match_rounds.id` | Integer | no (PK) | auto rowid | PK |
| `.round_number` | Integer | no | — | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `match_histories.id` | Integer | no (PK) | auto rowid | PK |
| `.round_id` | Integer | no | — | FK `match_rounds.id` |
| `.court_number` | Integer | no | — | — |
| `.team1_player1_id` | Integer | no | — | FK `participants.id` |
| `.team1_player2_id` | Integer | no | — | FK `participants.id` |
| `.team2_player1_id` | Integer | no | — | FK `participants.id` |
| `.team2_player2_id` | Integer | no | — | FK `participants.id` |
| `.team1_score` | Integer | yes | — | — |
| `.team2_score` | Integer | yes | — | — |
| `.score_text` | Text | yes | — | — |
| `.winner_team` | Integer | yes | — | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `bench_histories.id` | Integer | no (PK) | auto rowid | PK |
| `.round_id` | Integer | no | — | FK `match_rounds.id` |
| `.participant_id` | Integer | no | — | FK `participants.id` |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `match_sessions.id` | Integer | no (PK) | auto rowid | PK |
| `.status` | String(20) | no | Python `draft` | — |
| `.match_count` | Integer | no | Python `0` | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `.confirmed_at` | DateTime | yes | — | — |
| `.notification_sent_at` | DateTime | yes | — | — |
| `line_accounts.id` | Integer | no (PK) | auto rowid | PK |
| `.participant_id` | Integer | no | — | FK Participant, UNIQUE |
| `.line_user_id` | String(128) | no | — | UNIQUE |
| `.display_name` | String(100) | yes | — | — |
| `.active` | Boolean | no | Python `True` | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `.updated_at` | DateTime | no | Python `utc_now`, onupdate `utc_now` | — |
| `notification_subscriptions.id` | Integer | no (PK) | auto rowid | PK |
| `.session_id` | Integer | no | — | FK MatchSession |
| `.participant_id` | Integer | no | — | FK Participant |
| `.channel` | String(20) | no | Python `line` | composite UNIQUE |
| `.active` | Boolean | no | Python `True` | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `.updated_at` | DateTime | no | Python `utc_now`, onupdate `utc_now` | — |
| `line_link_tokens.id` | Integer | no (PK) | auto rowid | PK |
| `.token` | String(20) | no | — | UNIQUE |
| `.participant_id` | Integer | no | — | FK Participant |
| `.session_id` | Integer | no | — | FK MatchSession |
| `.used_at` | DateTime | yes | — | — |
| `.expires_at` | DateTime | no | caller sets +30min | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `match_notifications.id` | Integer | no (PK) | auto rowid | PK |
| `.session_id` | Integer | no | — | FK MatchSession; composite UNIQUE |
| `.match_count` | Integer | no | — | composite UNIQUE |
| `.channel` | String(20) | no | Python `line` | composite UNIQUE |
| `.status` | String(20) | no | Python `pending` | — |
| `.created_at` | DateTime | no | Python `utc_now` | — |
| `.sent_at` | DateTime | yes | — | — |
| `notification_delivery_logs.id` | Integer | no (PK) | auto rowid | PK |
| `.session_id` | Integer | no | — | FK MatchSession |
| `.participant_id` | Integer | no | — | FK Participant |
| `.match_count` | Integer | no | — | — |
| `.channel` | String(20) | no | Python `line` | — |
| `.status` | String(20) | no | caller required | — |
| `.error_message` | Text | yes | — | — |
| `.sent_at` | DateTime | no | Python `utc_now` | — |

### 3.2 Relationships, unique constraints and indexes

- `Participant.card` UNIQUE。
- `LineAccount.participant_id` UNIQUE、`LineAccount.line_user_id` UNIQUE。
- `NotificationSubscription(session_id, participant_id, channel)` named UNIQUE `uq_notification_subscription`。
- `LineLinkToken.token` UNIQUE。
- `MatchNotification(session_id, match_count, channel)` named UNIQUE `uq_match_notification`。
- これ以外のunique constraintはない。とくにround/session、round/court、bench participant、DeliveryLogにはuniqueがない。
- model定義上のexplicit indexは0件。PKとUNIQUEに伴うSQLite auto-indexだけが存在する。FK列にも自動indexは付かない。
- ORM cascadeは Participant→LineAccount/Subscription/Token/DeliveryLog、MatchSession→Subscription/Token/DeliveryLog/MatchNotification、MatchRound→MatchHistory/BenchHistoryに `all, delete-orphan`。D1 bindingではORM cascadeが存在しないためDDLまたはcommandで明示する。
- MatchHistory/BenchHistoryのParticipant FKにはParticipant側relationship/cascadeがない。履歴があるparticipantを物理削除する通常routeもない。

### 3.3 Application-level constraints

| Area | Current rule |
| --- | --- |
| Participant create | name non-empty; gender/level must exist in current config; card must be in available `ALL_CARDS`; computed weight; DB UNIQUE is final protection |
| Participant edit | route currently assigns name/gender/level/active directly; it does not revalidate or recompute weight. Migration must not silently change this behavior |
| Match draft | participant IDs exist; IDs do not repeat within/across courts or bench; courts normally 4 players; bench disjoint; fixed pair has two distinct integer IDs, no participant in two fixed pairs, and both members remain the same current pair |
| MatchRound | `round_number = match_state.match_count + 1`; duplicate round numbers may exist after session reset; latest is selected by highest ID |
| MatchHistory | court number starts at 1; player slots non-null; score/winner validation depends on `winner_only`/`score`, points, games, deuce, maximum; winner is null/1/2 |
| MatchSession | code uses status `draft`, `confirmed`, `closed`; DB does not CHECK it. `match_count` exists but confirm currently does not update it |
| Token | generated token intended unique; expiry 30min; unused, unexpired, linked participant active; same LINE user cannot link to another participant |
| Notification | channel currently `line`; notification status `pending`/`completed`; delivery status `pending`/`success`/`failed`/`skipped`; existing notification of any status blocks another send |

## 4. Proposed D1 schema

以下は設計DDLでありmigration fileではない。長さ指定はdocumentationとして残すがSQLite/D1はVARCHAR長を強制しない。全timestampは後述のUTC TEXT。BooleanはINTEGER 0/1。D1はforeign keyを常時有効相当で検査するため、親子削除を明示する。[D1 foreign keys](https://developers.cloudflare.com/d1/sql-api/foreign-keys/)

```sql
CREATE TABLE participants (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  gender TEXT NOT NULL,
  level TEXT NOT NULL,
  weight REAL NOT NULL,
  games_played INTEGER DEFAULT 0 CHECK (games_played IS NULL OR games_played >= 0),
  active INTEGER DEFAULT 1 CHECK (active IS NULL OR active IN (0, 1)),
  card TEXT NOT NULL UNIQUE
);

CREATE TABLE match_sessions (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'draft',
  match_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  notification_sent_at TEXT
);

CREATE TABLE match_rounds (
  id INTEGER PRIMARY KEY,
  round_number INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE match_histories (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES match_rounds(id) ON DELETE CASCADE,
  court_number INTEGER NOT NULL,
  team1_player1_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
  team1_player2_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
  team2_player1_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
  team2_player2_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
  team1_score INTEGER,
  team2_score INTEGER,
  score_text TEXT,
  winner_team INTEGER CHECK (winner_team IS NULL OR winner_team IN (1, 2)),
  created_at TEXT NOT NULL
);

CREATE TABLE bench_histories (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES match_rounds(id) ON DELETE CASCADE,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);

CREATE TABLE line_accounts (
  id INTEGER PRIMARY KEY,
  participant_id INTEGER NOT NULL UNIQUE REFERENCES participants(id) ON DELETE CASCADE,
  line_user_id TEXT NOT NULL UNIQUE,
  display_name TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE notification_subscriptions (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES match_sessions(id) ON DELETE CASCADE,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  channel TEXT NOT NULL DEFAULT 'line',
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CONSTRAINT uq_notification_subscription UNIQUE (session_id, participant_id, channel)
);

CREATE TABLE line_link_tokens (
  id INTEGER PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES match_sessions(id) ON DELETE CASCADE,
  used_at TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE match_notifications (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES match_sessions(id) ON DELETE CASCADE,
  match_count INTEGER NOT NULL,
  channel TEXT NOT NULL DEFAULT 'line',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  sent_at TEXT,
  CONSTRAINT uq_match_notification UNIQUE (session_id, match_count, channel)
);

CREATE TABLE notification_delivery_logs (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES match_sessions(id) ON DELETE CASCADE,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  match_count INTEGER NOT NULL,
  channel TEXT NOT NULL DEFAULT 'line',
  status TEXT NOT NULL,
  error_message TEXT,
  sent_at TEXT NOT NULL
);

CREATE TABLE runtime_state (
  state_key TEXT PRIMARY KEY CHECK (state_key IN ('current_match', 'current_draft')),
  state_json TEXT NOT NULL CHECK (json_valid(state_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at TEXT NOT NULL
);

CREATE TABLE app_config (
  config_key TEXT PRIMARY KEY CHECK (config_key = 'main'),
  config_json TEXT NOT NULL CHECK (json_valid(config_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_match_rounds_round_number_id
  ON match_rounds(round_number, id DESC);
CREATE INDEX idx_match_rounds_created_id
  ON match_rounds(created_at, id);
CREATE INDEX idx_match_histories_round_court
  ON match_histories(round_id, court_number, id);
CREATE INDEX idx_match_histories_winner
  ON match_histories(winner_team);
CREATE INDEX idx_bench_histories_round_id
  ON bench_histories(round_id, id);
CREATE INDEX idx_notification_subscriptions_targets
  ON notification_subscriptions(session_id, channel, active, participant_id);
CREATE INDEX idx_line_link_tokens_participant_session
  ON line_link_tokens(participant_id, session_id);
CREATE INDEX idx_delivery_logs_session_match_channel
  ON notification_delivery_logs(session_id, match_count, channel, participant_id);
```

`INTEGER PRIMARY KEY`を使い、不要な`AUTOINCREMENT`は付けない。migrationで既存IDを明示insertし、その後のID自動採番が`MAX(rowid)+1`になることをlocal/remote D1で検証する。新規adapterはPython-side default/onupdateに依存せず、値を明示bindする。

### 4.1 Concurrency-hardening adjunct（baselineとは分離）

現行schemaだけではsessionごとのround一意性と、`MatchRound INSERT → generated id → child INSERT`を一つのprepared-statement batchで安全に表現しにくい。D1の`batch()`はprepared statementsを事前に配列化し、結果は実行後に返すため、先頭statementの`last_row_id`を後続statementのbind値へその場で渡せない。`last_row_id`自体はD1Result metadataに存在する。[D1 return object](https://developers.cloudflare.com/d1/worker-api/return-object/)

実装前に次のadjunctを別migrationとして判断する。

```sql
ALTER TABLE match_rounds ADD COLUMN session_id INTEGER
  REFERENCES match_sessions(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX uq_match_round_session_round
  ON match_rounds(session_id, round_number)
  WHERE session_id IS NOT NULL;
CREATE UNIQUE INDEX uq_match_history_round_court
  ON match_histories(round_id, court_number);
CREATE UNIQUE INDEX uq_bench_history_round_participant
  ON bench_histories(round_id, participant_id);
```

新規roundは`session_id`必須、legacy migrated roundはsessionを正確に復元できない場合NULLを許す。confirm batchはparentを通常の`INSERT`（duplicateならbatch全体をconstraint errorでrollback）し、childrenは`SELECT id FROM match_rounds WHERE session_id=? AND round_number=?`で参照できる。これによりgenerated IDのapplication内受け渡しを避ける。`last_insert_rowid()`をstatement間で使う案は、D1 bindingが同一SQLite connection stateを保証するか公式記述を確認できないため採用せず **要PoC** とする。

## 5. Storage mapping

| Data | D1 | R2 | Secret/vars | Notes |
| --- | --- | --- | --- | --- |
| 10 model tables | yes | no | no | relational source of truth |
| confirmed/draft state | yes | no | no | `runtime_state` two rows |
| history dump body | no | yes | no | private object |
| admin-editable config | yes | no | no | one JSON row, versioned |
| `SECRET_KEY` | no | no | Worker Secret | production必須 |
| LINE channel secret/access token | no | no | Worker Secrets | logs/responseへ出さない |
| SMTP password | no | no | Worker Secret | SMTP移行は別設計 |
| SMTP host/port/security/username/from/timeout | no | no | vars（usernameをsecret扱いにしてもよい） | deploy environment config |
| `LINE_MESSAGING_ENABLED`, friend URL | no | no | vars | friend URLは公開可能、feature flagは環境依存 |
| public base URL/domain | no | no | vars / Worker route | admin-edit不可 |

## 6. Sync Flask ↔ async D1 architecture

### 6.1 Options

| Option | Code change | Flask compatibility | Tests | Binding access | Transaction | Maintainability / risk |
| --- | --- | --- | --- | --- | --- | --- |
| A. Flask routes async化 | very high: route→helper→data call graph | Flask async viewは可能でもWSGI/Workers組合せ要検証 | 多数書換え | route内`await` | async batch自然 | high risk。現行extension/同期helperとの混在 |
| B. WorkerでprefetchしFlaskへ渡す | high; route-specific parserが二重化 | Flask本体はsync維持 | Worker/Flask結合test増 | Worker entrypoint | write transactionがroute判断から離れる | dynamic form/redirect/validationに不向き |
| C. storage abstraction全体をasync化 | very high | sync routeから結局bridge必要 | 全data caller async化 | adapterへenv | 明示しやすい | 長期的には整然だがmigration量過大 |
| D. sync route内のrequest-local adapterが`run_sync` | medium | sync Flaskを維持 | 既存testを最大限再利用 | `request.environ["workers.env"].DB` | adapterの`batch()` command | **推奨**。D1固有挙動は要PoC |
| E. WSGI/FlaskをASGI/Worker-nativeへ一部置換 | very high | route/template互換リスク | route integration再構築 | native async | 自然 | storage移行とframework移行を混在させる |
| F. 別Worker/DOへservice RPC | high + deploy topology | Flask sync側bridgeは残る | cross-worker integration必要 | service binding | remote command単位 | coordinationが必要と判明した箇所だけ将来候補 |

推奨Dの根拠:

1. CloudflareはFlaskをPython WorkersのWSGI serverで公式サポートしている。
2. 公式Flask例は `request.environ["workers.env"]` からbindingを取得する。
3. 同例は `pyodide.ffi.run_sync` でbinding Promiseを同期handlerへ橋渡しする。
4. Python WorkerからD1/R2を含むbindingsへFFI経由でアクセスできることも公式に明記されている。[Python Workers FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)

PoCで確認するまで確定しない項目:

- D1 `.run()/.first()/.all()/.batch()` Promiseへの`run_sync`適用。
- `JsProxy` resultをPython dict/listへ変換する正しい位置とコスト。
- UNIQUE/FK errorのPython exception型・message・rollback結果。
- 1 sync request内で複数回`run_sync`してもevent loop/reentrancy errorがないこと。
- request cancellation / Worker timeout時のbatch結果。
- WSGI teardown後にbinding proxyを保持しないこと。

## 7. D1 binding propagation

推奨request flow:

```text
Worker fetch (async, self.env)
  -> workers.wsgi / environ["workers.env"]
    -> Flask before_request
      -> D1Storage(DB binding, R2 binding, run_sync) を flask.g に格納
        -> data named query / transaction command
```

- `flask.g` はrequest-local holderとして使う。binding自体をmodule globalへcacheしない。
- adapter factoryは `request.environ.get("workers.env")` を読み、D1 production adapterまたは明示的に設定されたSQLite test/local adapterを返す。
- data関数を直接unit testする場合は、Flask requestへ依存させずadapterを明示引数で渡せるescape hatchを用意する。通常callerは `get_storage()` を使う。
- `ContextVar` はFlask context外の深いhelperにも伝播できるが、WSGI/FFI境界でのcontext伝播を追加検証する必要があり第一候補にしない。
- request-local storageと`Flask g`は実質同じ責務。独自thread-localは作らない。
- 全routeへdependency引数を足す明示DIは純粋だが変更量が大きい。transaction command/data関数のtestだけ明示DIを採る。
- global mutable `current_db`は禁止する。isolate再利用、重複request、binding environment切替、test汚染の原因になる。

## 8. Transaction mapping

D1 `batch()` はstatementを順次・非並列で実行し、一つが失敗するとsequence全体をabort/rollbackするSQL transactionとして公式に説明されている。[D1 `batch()`](https://developers.cloudflare.com/d1/worker-api/d1-database/) ただし、外部HTTP/R2/SMTPはD1 transactionに含まれない。

### 8.1 Current and target boundaries

| Operation | Current DB transaction | Outside side effect / current gap | Required classification | D1 batch class |
| --- | --- | --- | --- | --- |
| confirm | Round insert+flush、History×court、Bench×bench、Participant games+1、Session status/confirmed_atを1 commit。session新規作成は事前別commit | match JSON write→draft delete→DB commit。LINEはcommit後best effort | DB+both state **must atomic** target; LINE best effort | current schema C; session-round adjunct + precomputed statementsなら **B** |
| revert | games-1、History/Bench/Round deleteを1 commit | draft write before DB; confirmed state write after DB | DB+both state **must atomic** target | stable session/round keyがあれば **B**、なければC |
| reset_match | close session commit、games reset commit、new session commit | match state writesとdraft deleteが各別 | session close/new + games + stateはtargetでatomic | **B** precompute/batch |
| reset_db | all 10 table deletes are one commit | dump best effort before、state reset best effort after、email after | relational delete must atomic; R2/email best effort/current semantics | D1 delete **B**; whole operation **C** cross-store |
| score one/round | one commit（roundはvalidation失敗時rollback） | none | must atomic per submitted form | A(single) / B(round batch) |
| account link | Account upsert、Subscription upsert、Token usedを1 commit | LINE reply after DB | must atomic; reply best effort | **B** with conditional unused token guard |
| notification | existence read; notification+pending logs commit; LINE pushes; statuses+completed commit | per-recipient HTTP cannot rollback | reservation must atomic; sends best effort; finalization atomic | setup/finalize B、whole operation C |
| dump_and_clear | dump attempt then 3 deletes commit | filesystem/R2 + mail outside | current backup best effort; delete must atomic | C overall |
| settings save | whole JSON file truncate/write | partial file possible on crash | whole config atomic | A conditional UPSERT |

### 8.2 Confirm detail

Target confirm command inputs are a validated draft snapshot, `expected_draft_version`, `session_id`, `next_match_count`, timestamp and calculated participant IDs. It should atomically:

1. reject an already-confirmed `(session_id, round_number)` via unique constraint;
2. insert MatchRound;
3. insert MatchHistory per court and BenchHistory per bench participant;
4. increment `games_played` exactly once;
5. set MatchSession confirmed fields and `match_count`;
6. replace `current_match` JSON and increment its version;
7. remove `current_draft` only if it is the validated version.

`batch()` alone does not provide application code a mid-batch result for rebinding, and a zero-row optimistic UPDATE does not automatically fail the rest of a batch. Therefore stale-draft rejection must be solved by one of: (a) schema trigger/constraint that calls `RAISE(ABORT)` on version mismatch, (b) a transaction guard table/statement proven by PoC, or (c) serialization in a per-session Durable Object. Choosing among these is an explicit unresolved design gate; silent last-write-wins is not acceptable.

### 8.3 Notification duplicate prevention

Required invariant remains:

```sql
UNIQUE (session_id, match_count, channel)
```

Reservation pattern:

```sql
INSERT INTO match_notifications
  (session_id, match_count, channel, status, created_at)
VALUES (?, ?, ?, 'pending', ?)
ON CONFLICT(session_id, match_count, channel) DO NOTHING
RETURNING id;
```

Only a returned row owns the send. A separate `SELECT` followed by `INSERT` is not sufficient. `INSERT OR IGNORE` may also ignore constraints other than the intended duplicate, so the targeted `ON CONFLICT(...) DO NOTHING` form is preferred. Pending DeliveryLogs are created in the same reservation batch. LINE HTTP calls occur after commit. Each result is then updated and MatchNotification set `completed` in a final batch. The current partial-failure meaning is preserved: failed recipients are logged, successful recipients stay successful, and confirm itself succeeds.

Crash after reservation but before completion currently leaves a pending notification that blocks duplicates. Initial migration should preserve that at-most-once attempt behavior. A lease/retry workflow is a future reliability change, not a migration requirement. Adding a DeliveryLog composite unique is desirable hardening but is a behavior/schema change and should be a separate decision.

## 9. match_state migration

| Option | Atomicity/read cost | Compatibility | Assessment |
| --- | --- | --- | --- |
| A. single-row JSON | one UPSERT, easily included in batch; one read | exact existing shape | **recommended** |
| B. columns + child tables | stronger SQL typing, many statements/joins | serializer needed; matches nested | unnecessary complexity now |
| C. rebuild from relational | eliminates duplicate state | current `match_active`, court_count, empty confirmed state, current session semantics are not fully derivable | not migration-compatible |

Use `runtime_state.state_key='current_match'`; keep JSON key names and participant-ID arrays unchanged. Store canonical compact JSON, UTC timestamp, and row `version`. API/template callers continue to receive the same Python dict. State read frequency is high and write frequency low; one indexed PK read is suitable. Confirm/revert/reset/session switching can include this row in the same D1 batch as relational changes, which fixes a current consistency gap. This atomicity improvement is recommended migration work because filesystem state cannot survive Workers, but any additional semantic cleanup is future work.

## 10. draft_state migration

| Option | Atomicity/editing | Compatibility | Assessment |
| --- | --- | --- | --- |
| single-row JSON + version | one conditional update; preserves optional keys/fixed pairs | exact | **recommended** |
| column split + JSON matches | court/version easier to query but mixed model | moderate | no current query benefit |
| full relation | fine-grained edits/constraints | large schema and transaction surface | reject for initial migration |

Use `state_key='current_draft'`; absent row means no active draft, matching absent file. Every edit reads version and writes `version+1`. An update with stale version returns conflict and the route asks the admin to reload; it must not overwrite a newer edit. New generation replaces the row and clears previous `fixed_pairs` unless explicitly supplied. Confirm consumes exactly the version it validated. Viewer reads do not mutate. `fixed_pairs` remains draft-only.

## 11. History dump → R2

### 11.1 Current behavior

- filename: `match_history_<reason>_YYYYMMDD_HHMMSS_microseconds.json` in UTC; regex allows alphanumeric/underscore reason。
- reasons in production: `manual_dump`, `manual_dump_and_clear`, `clear_all_data`。
- schema version 1 with `dumped_at`, `reason`, ordered `rounds`; each round has id/number/time, matches including participant id/name/card and scores/winner/time, and bench participant id/name/card/time。
- create timing: manual dump; before history delete; before all-data delete。
- dump failure is best effort: `dump_and_clear` and `reset_db` still delete DB records and warn。Email failure also does not restore data。
- archive UI lists files, reads metadata/body and renders detail. There is currently **no archive download endpoint**; `/download_template` is only the participant CSV template。
- SMTP attachment currently rereads the JSON path and `Path.read_bytes()`。

### 11.2 R2 design

Bucket is private and bound as e.g. `HISTORY_DUMPS`. R2 Workers API provides bound `get`, `put`, `list`, `delete`; writes/deletes are strongly consistent, and listing must paginate using `truncated`/`cursor` rather than object count.[R2 Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/)

```text
history-dumps/YYYY/MM/<existing-filename>
```

- Preserve existing filename exactly. Derive `YYYY/MM` from filename UTC timestamp; legacy migration derives from filename first and object mtime only as fallback。
- `Content-Type: application/json; charset=utf-8`。
- `Content-Disposition: attachment; filename="<existing-filename>"` even though current UI only views; this keeps a safe future download path。
- custom metadata: `schema-version=1`, `reason`, `dumped-at`; do not place participant names/cards in metadata。
- `put`: serialize once to UTF-8 bytes, calculate SHA-256 for migration validation, then put。Small JSON dumps do not need multipart。
- `list`: prefix `history-dumps/`, include custom metadata, follow cursors; sort UI by `dumped-at`/uploaded descending after collection。If volume makes full listing expensive, add a D1 archive index later; do not create a cross-store consistency problem initially。
- `get`: validate filename with the existing regex, deterministically construct key, fetch bytes, parse/normalize as today。
- `delete`: storage adapter supports exact validated key, but no application route is added because current UI cannot delete archives。
- retention: no automatic expiry initially。History dumps are backups; lifecycle deletion requires a separately approved retention policy。

R2 object write and D1 delete cannot share a transaction. Preserve current best-effort semantics for compatibility, but log an operation ID/key so operators can correlate “R2 failed, D1 cleared”. A future safer product decision could make successful R2 put mandatory before delete; that is not part of storage migration.

### 11.3 SMTP relationship

Recommended adapter signature accepts `(recipient, subject, body, filename, content_bytes, content_type)`。After R2 put, retain the already serialized bytes for immediate mail; when mailing an existing object, use `R2.get().arrayBuffer()` and convert once. Do not write a temporary Worker file. This removes `dump path -> SMTP attachment` coupling while preserving attachment bytes and name。

Whether Python `smtplib` is production-viable in Workers is outside this storage design and **要PoC/別PR**。If replaced with an email provider, pass the same bytes directly. Mail remains after D1/R2 operations and best effort。

## 12. Config / secrets

Actual repository schema was inspected by keys/types only; values from local `config.json` are not reproduced。

| Config item | Recommended store | Reason |
| --- | --- | --- |
| `level_map` | D1 `app_config` | admin editable, shared immediately |
| `gender_weight` | D1 `app_config` | admin editable; participant registration depends on it |
| `score_input_mode` | D1 `app_config` | admin editable and route behavior |
| `scoring_system.*` | D1 `app_config` | update as one validated document |
| `consecutive_play_limit` | D1 `app_config` | admin editable; match logic read |
| `paypay_links.*` | D1 `app_config` | admin editable; not a credential, though repository exposure prohibited |
| `paypay_link_expirations.*` | D1 `app_config` | must update with links |
| `history_dump_email.enabled/recipient` | D1 `app_config` | admin editable; recipient is operational data, not SMTP credential |
| normalization defaults | fixed code defaults | backward compatibility when keys absent/invalid |
| `SECRET_KEY` | Worker Secret | cookie signing secret |
| `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN` | Worker Secrets | credentials |
| `SMTP_PASSWORD` | Worker Secret | credential |
| `SMTP_HOST/PORT/SECURITY/TIMEOUT/FROM_EMAIL`, feature flags, base URL | Worker vars | environment/deploy concerns; promote username to Secret if policy requires |
| `LINE_BOT_FRIEND_URL` | Worker var | public link |

`app_config` updates use `WHERE version=?` and increment version; admin POST receiving stale config returns a conflict/reload message. Config is not cached in module global. Read-per-request is acceptable initially; request-local cache may avoid repeated reads within one request. KV is not proposed because settings writes must be immediately visible and the app already needs D1.

## 13. Concurrency / race conditions

| Operation | Race | Minimum protection | DO? |
| --- | --- | --- | --- |
| participant registration | two clients choose same card | `Participant.card UNIQUE`; handle constraint deterministically | no |
| CSV upload | conflict with registration/upload | batch + unique; precheck is advisory | no |
| draft edit/generation | stale browser overwrites new draft | runtime row version/CAS; confirm must consume expected version | only if CAS cannot make confirm atomic |
| confirm | double submit; edit during confirm; state count read-modify-write | session/round unique, transaction batch, draft version guard | **conditional candidate** |
| revert | two reverts or confirm vs revert | versioned current state + stable round key + batch | conditional candidate |
| reset | reset vs confirm/edit/notification | state version + transaction command; maintenance flag for reset_db | conditional candidate, especially reset_db |
| LINE reservation | two confirms/workers send same round | unique + targeted `ON CONFLICT DO NOTHING RETURNING` | no |
| DeliveryLog finalize | late/duplicate worker update | update by notification/session/participant and expected `pending`; optional unique | no initially |
| complete line link | token used twice/account conflict | conditional token update, account/subscription unique, single batch | no |

D1 constraints are sufficient where a single statement/batch can make contention fail safely. Optimistic concurrency is required for runtime/config read-modify-write. Durable Object is considered only for the small set of event-wide state transitions if PoC demonstrates no reliable D1-only stale-version abort pattern. Cloudflare positions Durable Objects for coordination among clients and recommends one object per logical coordination unit, which maps here to event/session rather than participant/table.[Durable Objects overview](https://developers.cloudflare.com/durable-objects/)

`reset_db` is unusually broad and currently unauthenticated by product specification. It should use an application maintenance marker in D1 to reject new mutation commands during reset. This does not add administrator login; network exposure/security remains a deployment concern.

## 14. Datetime representation

Use fixed-format ISO-8601 UTC TEXT:

```text
YYYY-MM-DDTHH:MM:SS.ffffffZ
```

Reasons:

- maps directly to aware Python `datetime` and current dump ISO strings;
- human-readable migration evidence;
- fixed width and UTC make lexical order equal chronological order;
- avoids JavaScript millisecond epoch vs Python microsecond ambiguity;
- D1/SQLite has no native datetime type。

Adapter rules:

1. accept only aware datetime at new write boundaries; convert to UTC;
2. serialize exactly six fractional digits and `Z`;
3. parse legacy SQLite naive DateTime as UTC, matching current `serialize_datetime` behavior;
4. convert old state timestamps with offsets to UTC;
5. never store local Tokyo time in D1; convert only for display/business-date logic;
6. compare expiry using normalized UTC strings or parsed aware datetime, never mixed naive/aware values。

Unix epoch INTEGER is compact and easy for arithmetic, but would require more conversion in dumps/UI and risks seconds/milliseconds mismatch. It is not selected.

## 15. Local development

| Option | Fidelity | Existing workflow | Maintenance |
| --- | --- | --- | --- |
| A. local SQLite, production D1 | low for binding/transaction semantics | preserves `python app.py` | permanent behavior split |
| B. local Wrangler D1 | high | requires `pywrangler dev` for integration | recommended production-parity lane |
| C. tests only SQLite adapter | good for fast semantics, not binding | preserves pytest | recommended fast lane, not sole lane |
| D. D1-compatible fake | superficially convenient | fast | rejects: fake can mask batch/FFI differences |

Recommended two lanes:

- fast: `python app.py` and most `pytest` use temporary SQLite adapter while migration is active and for pure/local development;
- parity: `pywrangler dev` with persistent local D1 binding runs storage contract and route integration tests。

Do not keep two independently evolving business implementations. Named data functions/commands share validation/serialization; only the execution adapter differs. `STORAGE_BACKEND=sqlite|d1` is acceptable temporarily for migration, tests and local CLI. Production must fail closed unless explicitly `d1`; no silent fallback to ephemeral SQLite. Remove/generalize the flag after cutover, retaining SQLite only where explicitly justified for tests/local development.

## 16. Test strategy

| Current category | Examples | D1 migration treatment |
| --- | --- | --- |
| pure logic | score parsing/calculation, PayPay expiry, draft validators/pair utilities with supplied data | keep unchanged |
| DB-dependent | `test_data_access`, notification models, stats, session/history portions | run storage contract against SQLite adapter; add local D1 integration |
| state-file dependent | `test_match_state`, `test_draft_state`, match draft/reset/session/logic portions | retain JSON-shape assertions; replace filesystem setup with state adapter fixture; add D1 version/CAS tests |
| route integration | match draft/history/LINE/flash/paypay/secret/runtime tests | keep Flask test client with SQLite adapter; selected flows through local Worker+D1 |
| LINE notification | `test_line_notification_routes`, notification model tests | preserve message/partial failure tests; add concurrent reservation and pending/finalization D1 tests |
| history dump/mail | `test_match_history`, `test_mail_sender` | keep serializer tests; fake object adapter for unit tests; local R2 binding integration for put/list/get bytes |
| Cloudflare PoCs | existing `test_cloudflare_*_poc` | keep as separate evidence; next D1 PoC adds focused test, not production behavior |

Contract tests should cover each named data query/command with the same synthetic fixture against SQLite and local D1 where meaningful. Exact SQLAlchemy object identity/lazy loading assertions should be replaced only where they test an implementation detail; route-visible values/order/errors remain. Do not delete the existing suite wholesale.

Required D1-specific tests:

- all unique/FK/CHECK constraints and expected error mapping;
- batch rollback when middle statement fails;
- generated parent ID strategy;
- double confirm and stale draft conflict under parallel requests;
- double notification reservation returns one owner;
- config/draft CAS;
- UTC lexical ordering and legacy datetime transform;
- migrated row counts, IDs, nulls, booleans and foreign-key check;
- R2 pagination, invalid filename rejection, byte-for-byte body, metadata and missing object behavior。

## 17. Migration / rollback

### 17.1 Procedure

1. **Schema preparation**: approve baseline DDL and concurrency adjunct; create migrations only in implementation phase; apply to isolated local/preview D1。
2. **SQLite snapshot**: maintenance-safe copy/backup of EC2 DB; separately snapshot `match_state.json`, `draft_state.json`, sanitized config and dump file inventory/hashes。
3. **Export/transform**: use SQLite export script (not raw `.db` upload); preserve integer IDs; normalize Boolean and UTC timestamps; detect invalid/orphan FK and duplicate assumptions before import。
4. **D1 import**: import parent-before-child, using D1 migration/import tooling and `PRAGMA defer_foreign_keys` only where necessary; D1 does not accept a raw SQLite file directly.[D1 import/export](https://developers.cloudflare.com/d1/best-practices/import-export-data/)
5. **Validation**: table row counts, min/max IDs, per-table checksums of canonical exported rows, unique queries, `PRAGMA foreign_key_check`, representative JOIN/order results, notification invariants。
6. **State/config**: transform confirmed/draft JSON to UTC canonical JSON rows with version 1; validate every participant/session ID; insert dynamic config row; set vars/secrets out of band。
7. **R2 migration**: upload each existing filename under derived year/month key; verify size and SHA-256/body; compare object count and filename set; do not delete source dumps。
8. **Shadow/canary**: run synthetic/read-only comparisons against D1; no production domain and no real writes during PoC. Before cutover, canary selected read routes behind restricted environment。
9. **Final cutover**: announce maintenance window, stop mutation traffic, take final delta/full snapshot, import/validate again, deploy Worker with explicit D1/R2 bindings and secrets, smoke participant/state/admin/history/LINE-disabled paths, then reopen writes。
10. **Post-cutover**: monitor constraint errors, stale conflicts, latency, notification reservations and R2 failures; retain EC2 snapshot and R2 source copies for approved retention period。

### 17.2 Rollback

- Before writes reopen: route traffic back to EC2/SQLite; source snapshot remains authoritative。
- After D1 production writes: rollback is not a simple traffic switch because data diverges. Enter maintenance, export D1 delta/full data, reconcile into a cloned SQLite DB, validate, then switch. Do not dual-write automatically。
- Keep deployment rollback separate from data rollback. D1 schema migrations must have explicit forward-fix/reverse plan; destructive migration follows verified backup。
- R2 migration is additive; source dump files remain until validation and retention approval, so R2 rollback is switching the archive adapter back, not deleting objects。

## 18. Risks and unresolved questions

Top risks:

1. **sync/async bridge**: official `run_sync` Flask pattern exists, but D1 result/error/batch behavior is unproven in this app (**first PoC gate**).
2. **cross-resource atomicity and current gaps**: current DB + two JSON files are non-atomic; R2/LINE/SMTP can never join D1 transaction.
3. **generated round ID + stale draft**: baseline schema lacks session-round key and a zero-row CAS does not abort batch; requires adjunct/guard/DO decision.
4. **ORM leakage**: routes/helpers rely on mutation and relationships; incomplete boundary migration would leave hidden SQLAlchemy access and N+1 queries.
5. **cutover/rollback divergence**: writes after D1 cutover cannot be rolled back to stale EC2 SQLite without reconciliation.

Other unresolved items:

- exact Python D1 exception types and JS proxy conversion;
- D1 binding support for `RETURNING` in the selected Python runtime compatibility date;
- best D1-only assertion pattern for expected state version;
- whether `match_rounds.session_id` can be reconstructed for any historical rows; otherwise leave legacy NULL;
- Python SMTP viability and outbound networking on target Workers runtime;
- operational retention policy for source SQLite backups and R2 history dumps;
- whether read replication will be enabled. Initial correctness path should use primary; Sessions/bookmarks are needed if replica reads are later enabled for sequential consistency.[D1 read replication](https://developers.cloudflare.com/d1/best-practices/read-replication/)

## 19. Recommended implementation phases

1. **D1 binding + minimal isolated PoC**: prove WSGI `workers.env`, `run_sync`, insert/select persistence, batch rollback/errors; no production data/domain。
2. **Storage contract and request-local provider**: named reads/commands, SQLite adapter, D1 adapter skeleton; no route behavior change。
3. **Participant + config**: participant read/write/API raw sqlite removal; D1 app config/Worker secrets split; contract tests。
4. **Match/session/history relational**: schema adjunct decision, reads/scores, confirm/revert transaction commands without state cutover yet in isolated integration tests。
5. **Runtime state**: both JSON shapes into D1, versioning, confirm/revert/reset atomic batches; remove production filesystem state。
6. **LINE relational/notification**: link/subscription commands, unique reservation, partial-failure finalization。
7. **History dumps R2**: byte serializer/object adapter/list/detail/email byte interface; retain current best-effort semantics。
8. **Reset and destructive-flow hardening**: reset_match/reset_db maintenance and cross-store warnings; end-to-end concurrency tests。
9. **Migration tooling and rehearsal**: export/transform/import/validate/R2 copy scripts, synthetic then cloned production snapshots。
10. **Production cutover and dual-backend retirement**: maintenance cutover, validation, monitoring, documented rollback window; remove production SQLite fallback。

This ordering moves config with Participant early because registration depends on config, and moves match state immediately after relational match commands so no long-lived hybrid production state is introduced. LINE follows confirmed state because notification messages consume it. R2 is later because it is cross-store and not required for the core request path except destructive backups。

## 20. Next D1 PoC

### Scope

Create one new isolated PoC Worker, not application production code/domain:

```text
WorkerEntrypoint.fetch (async)
  -> current Flask app / workers.wsgi
    -> sync PoC routes
      -> request.environ["workers.env"].DB
        -> run_sync(D1 prepared statement Promise)
```

Use a new preview/local D1 database and synthetic table/name only. Do not read, bundle or fingerprint real DB/config/state/dumps beyond existing safety guard assertions.

PoC routes:

- `POST /d1-poc/participants`: bind synthetic name/card, INSERT, return generated ID/constraint result。
- `GET /d1-poc/participants/<id>`: SELECT by prepared statement and return shape。
- `POST /d1-poc/batch-failure`: two safe synthetic writes with a deliberate middle UNIQUE failure; verify neither commits。
- `GET /d1-poc/status`: table/count only, no env or credentials。

### Questions the PoC must answer

1. Does sync Flask obtain the correct per-request binding through `workers.env`?
2. Does `run_sync` work for D1 `run/first/all/batch` without nested-loop or reentrancy failure?
3. How are D1Result and rows converted from `JsProxy` to Python values?
4. What are `meta.last_row_id`, `changes`, and `RETURNING` shapes in local and deployed Worker?
5. How do UNIQUE/FK failures surface, and does batch rollback every prior statement?
6. Does inserted data survive a new request, idle interval and same-code redeploy?
7. Can two concurrent inserts for the same unique card produce exactly one owner and one deterministic conflict?

### Success criteria

- current Flask app and its WSGI path are used;
- D1 binding, not filesystem SQLite or D1 REST API, is used;
- synthetic data only;
- participant-like INSERT and SELECT succeed;
- request-to-request and post-redeploy persistence succeeds;
- prepared binding prevents string interpolation;
- deliberate batch failure leaves zero partial rows;
- duplicate race leaves one row;
- local and dedicated workers.dev deployment evidence is sanitized;
- no production domain, real DB/config/state/dump, R2, KV or Durable Object is touched。

Failure of `run_sync` on D1 moves the architecture decision back to async call-graph conversion or a Worker-native/service boundary. Success promotes option D and unlocks the storage boundary phases; it does not by itself approve schema creation or production migration。

## Appendix A. Decision summary

### Inventory

- production DB access: 88 syntactic call sites; raw sqlite3: 1。
- state: 44 high-level call sites; 5 direct file I/O primitives。
- history dumps: 10 filesystem operation call sites across 8 functions。

### Architecture

- storage boundary: existing named `data/` reads + a small set of use-case transaction commands; request-local adapter; no generic repository/service stack。
- sync/async: Flask sync + `workers.env` + adapter-local `run_sync`, pending D1 PoC。
- Durable Object: only conditional for same-event confirm/revert/reset/draft serialization if D1 guard strategy is insufficient。

### Migration targets

- D1: all relational tables, confirmed/draft runtime rows, admin-editable config。
- R2: history dump JSON bodies。
- Worker Secrets: Flask/LINE/SMTP credentials。
- Worker vars: non-secret deploy/environment values and feature flags。
- SQLite: temporary local/test adapter, never production Worker persistence。

## Appendix B. Complete production access index

### B.1 Relational syntax count by file

This is the reproducible breakdown of the 88 call sites defined in section 2.1.

| File | Call sites | Functions / responsibility |
| --- | ---: | --- |
| `app.py` | 5 | `ensure_database_tables`, `ensure_match_history_score_text_column` |
| `data/participants.py` | 5 | `get_participant_by_card`, `get_all_participants`, `get_participants_ordered_by_card`, `get_participants_by_ids`, `get_active_participants` |
| `data/match_history.py` | 6 | `get_latest_match_round_with_matches`, `get_match_rounds_for_dump`, `get_latest_match_round`, `get_match_round_with_matches`, `get_match_history_by_id`, `get_match_rounds_with_details` |
| `data/match_sessions.py` | 1 | `get_match_session_by_id` |
| `data/line_notifications.py` | 8 | `get_notification_subscription`, `get_line_notification_targets`, `get_line_link_token_with_details`, `get_conflicting_line_account`, `get_line_account_for_participant`, `get_past_line_subscription`, `get_line_match_notification`, `get_line_link_token` |
| `logic.py` | 1 | `get_consecutive_player_ids` |
| `routes/admin.py` | 5 | `upload_csv`, `reset_db` |
| `routes/helpers.py` | 26 | `send_match_confirmed_line_notifications`, `complete_line_link`, `clear_all_data_records`, `clear_match_history_records`, `apply_round_score_updates` |
| `routes/history.py` | 4 | two single-match score routes, `dump_and_clear_match_history` |
| `routes/line.py` | 5 | `start_line_notification`, `unsubscribe_line_notification` |
| `routes/match.py` | 10 | `confirm_match`, `revert_match_to_draft` |
| `routes/participant.py` | 3 | `register`, `participant_view` |
| `utils/db_utils.py` | 1 | raw `get_all_participants` |
| `utils/match_session.py` | 3 | `ensure_current_match_session`, `close_current_match_session` |
| `utils/pair_optimizer.py` | 1 | `get_historical_pair_counts` |
| `utils/reset.py` | 2 | `reset_match_state` |
| `utils/stats.py` | 2 | `calculate_participant_win_stats` |
| **Total** | **88** | — |

Dirty ORM attribute updates in `participant_view`, confirm/revert, subscription/account/link handling and score helpers do not contain a query/commit call on every assignment line. They are nevertheless included in the semantic inventory in section 2.2; the count is syntax-based, not a count of mutated columns.

### B.2 State callers

- confirmed state (24 calls): `logic.get_previous_bench_ids`, `logic.get_consecutive_player_ids`, `routes.api.api_match_state`, `routes.helpers.get_match_count`, `render_index_view`, `send_match_confirmed_line_notifications`, `routes.match.match_form`, `edit_matches`, `confirm_match`, `revert_match_to_draft`, `match_result`, `match_draft`, `utils.match_session.get_current_session_id`, `ensure_current_match_session`, `utils.reset.reset_match_state`, `clear_match_runtime_state`, plus internal `save_match_state_full -> load/save` calls.
- draft state (20 calls): `routes.helpers.get_match_count`, `render_index_view`, `routes.match.match_form`, `edit_matches`, `optimize_pairs`, `swap_players`, `confirm_match`, `revert_match_to_draft`, `update_court_count`, `match_result`, `match_draft`, `utils.reset.reset_match_state`, `clear_match_runtime_state`, plus `get_active_draft -> load_draft_state`.
- direct filesystem primitives: match state read/write `open` 2; draft state read/write `open` 2 and `os.remove` 1.

### B.3 History archive and config I/O

- History archive functions: `get_match_history_dump_dir`, `get_match_history_archive_path`, `build_match_history_archive_metadata`, `list_match_history_archives`, `load_match_history_archive`, `dump_match_history_to_json`, `send_history_dump_email_if_enabled`, `utils.mail_sender._build_message`.
- History callers: `routes.admin.reset_db`; `routes.history.dump_match_history`, `dump_and_clear_match_history`, `admin_match_history_archives`, `admin_match_history_archive_detail`.
- Config I/O functions: `utils.config.load_raw_config` and `save_config`. Consumers are participant registration/thanks, CSV upload, admin settings, match generation/edit/scoring/history rendering, LINE success message and history-dump email settings.
