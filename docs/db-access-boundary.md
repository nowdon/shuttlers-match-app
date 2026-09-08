# DB access boundary 整理

## 基準と方針

- 基準: `origin/develop` の `5584a19`（PR #66 Blueprint 分割）。開始時は `develop`、未commit変更なし。`git fetch origin` 後に `codex/organize-db-access-boundary` を作成。
- 変更前 baseline: `pytest` **287 passed in 8.46s**。
- `routes -> data -> models` の名前付き読み取り関数を導入。ORM object を返す。
- data は Flask request / template / routes / current_app を import しない。SQLAlchemy の実行には従来どおり app context が必要。
- 書き込み・commit / rollback / flush・削除順序は既存の呼び出し元に保持。ORM attribute 更新や relationship access も残す。

## 変更前 DB access inventory

実装前に `.query`、`db.session`（query/get/add/delete/commit/rollback/flush を含む）、`.filter(`、`.filter_by(`、`.options(`、`.join(`、`.delete(`、`sqlite3`、`db.engine`、`inspect(`、`text(` を Python source 全体で検索した。文字列の join、path.join、app_context、score_text 等の一致はDBアクセスから除外した。

| カテゴリ | 主な所在と用途 | 今回の扱い |
| --- | --- | --- |
| Participant | helpers: カード順一覧、カード照合、通知・dump・表示用ID照合／participant: 登録の使用済カード、カード照合／admin: CSV登録の使用済カード／match: draft・confirm・revert・active一覧 | route/helper の read を `data/participants.py` へ。card uniqueness、active、games_played の更新処理は保持 |
| MatchRound / MatchHistory / BenchHistory | helpers: 最新roundのmatches、dump用時刻昇順＋ID昇順とmatches/bench eager load／history: ID指定score取得、時刻降順＋ID降順一覧／match: revert対象round | read を `data/match_history.py` へ。confirmでの追加、score保存、revert削除を保持 |
| MatchSession | `utils/match_session.py`: JSON session_idを解決し `session.get`、必要時作成、close／match/helpers: confirm・通知時の属性更新 | lookupのみ `data/match_sessions.py` へ。JSON連携、draft/confirmed/closed、作成・commitは元の場所 |
| LINE関連 | helpers: subscription lookup、過去session照合、Participant/LineAccount/Subscription join、重複通知照合、token照合・eager load、競合アカウント照合。line/helpers: account・subscription・token・notification・delivery log保存 | read を `data/line_notifications.py` へ。DeliveryLogに独立したread queryはなく、追加と削除を保持 |
| DB初期化 / schema compatibility | `app.py`: create_all、inspect(db.engine)、score_textのALTER TABLE、execute(text(...))、commit/rollback。`init_db.py` は初期化を呼び出す | SQLite schema互換処理として保持 |
| reset / bulk delete | helpers: 全10モデル削除、履歴3モデル削除／match: round配下bench・matches削除／utils/reset: 参加者一覧からgames_played更新とcommit | 削除順・transactionをそのまま保持 |
| tests用直接アクセス | tests全体: fixture作成、drop/create_all、モデル照合、DB書き込み、SQL計数、schema互換テストのraw sqlite3 | テストはDB境界を検証するため直接アクセスを維持。既存fakeの適用先だけdataへ拡張 |
| その他 | `logic.py`: ID降順の直近round、limit／`utils/pair_optimizer.py`: 全MatchHistory／`utils/stats.py`: 全Participant、winner_teamが1/2のMatchHistory／`utils/db_utils.py`: 独立sqlite3接続でAPI用5列取得 | 組み合わせ・統計・APIの既存経路として保持。今回の主眼はpresentation側のquery境界 |

## 新しいモジュールと関数

### data/participants.py

- `get_all_participants()` — 全件、追加の並び替えなし。
- `get_participants_ordered_by_card()` — 画面のcard昇順。
- `get_participant_by_card(card)` — 完全一致のfirst、未発見はNone。404はhelperの責務。
- `get_participants_by_ids(participant_ids)` — IDのIN検索。入力順や重複を結果に反映させない従来動作を維持。
- `get_active_participants()` — `active=True`。

### data/match_history.py

- `get_latest_match_round(round_number)` — round_number一致、ID降順first。revert向けlazy loadを保持。
- `get_latest_match_round_with_matches(round_number)` — 同条件＋matchesのselectinload。
- `get_match_round_with_matches(round_id)` — ID一致first＋matchesのselectinload。
- `get_match_history_by_id(match_history_id)` — session.get。
- `get_match_rounds_for_dump()` — created_at昇順、ID昇順、matches/bench_playersのselectinload。
- `get_match_rounds_with_details()` — created_at降順、ID降順、同じselectinload。

### data/match_sessions.py

- `get_match_session_by_id(session_id)` — session.getのみ。現在sessionの選択・作成は行わない。

### data/line_notifications.py

- `get_notification_subscription(participant_id, session_id)` — session、participant、channel=line。再有効化用にinactiveも返す。
- `get_past_line_subscription(participant_id, session_id)` — 他sessionのline登録。active条件を追加しない。
- `get_line_notification_targets(session_id)` — Participant/LineAccount/Subscriptionのjoin、3者のactive、session_id、channel=line。
- `get_line_match_notification(session_id, match_count)` — session/count/channelによる重複防止。status条件を追加しない。
- `get_line_link_token(token_value)` — token完全一致、発行時の重複確認。
- `get_line_link_token_with_details(token_value)` — 同照合＋participant/sessionのselectinload。有効期限・使用済み判定はhelper側。
- `get_conflicting_line_account(line_user_id, participant_id)` — 同じLINE user、別participant。inactiveも対象。
- `get_line_account_for_participant(participant_id)` — participant一致、inactiveも対象。

## 削減したreadと残した直接アクセス

ASTで `.query...all/first` と `db.session.get` の呼び出し箇所を集計。

| ファイル | 変更前read | 変更後read |
| --- | ---: | ---: |
| routes/helpers.py | 16 | 0 |
| routes/participant.py | 3 | 0 |
| routes/admin.py | 1 | 0 |
| routes/match.py | 9 | 0 |
| routes/history.py | 5 | 0 |
| routes/line.py | 0 | 0 |
| 合計 | 34 | 0 |

`utils/match_session.py` のsession.getも1箇所移動した。関数内で使用するORM relationshipの遅延読み込みや、APIからutils経由のraw sqlite3はこの件数に含まない。

残った直接アクセスの全呼び出し位置は末尾に記載する。理由は以下のとおり。

- helpers: 通知とLINE連携のadd/flush/commit/rollback、score保存のcommit/rollback。通知の二段階保存、partial failure、重複防止を維持するため。
- helpers: `clear_all_data_records` の10モデル削除と `clear_match_history_records` のbench→matches→round削除。既存順序と呼び出し元のcommitを維持するため。
- match: confirmのround/matches/bench追加、flush、commit/rollback。revertのbench→matches→round削除、commit。JSON保存との失敗処理・games_played整合性を保持するため。
- history: score・dump and clearのcommit/rollback。保存とバックアップの境界を保持するため。
- participant/admin: 登録・CSV追加・参加状態変更・resetのadd/commit/rollback。既存保存単位を保持するため。
- line: subscription/token追加、登録/解除のcommit。既存連携処理を保持するため。
- api: 直接SQLなし。`utils.db_utils.get_all_participants` への既存委譲を保持。
- `get_active_line_account` の `participant.line_account` 等、ORM relationship accessは残存。今回はDTO化・relationshipの全面置換を行わない。

## utils/db_utils.py と今後の障壁

raw sqlite3は残した。この関数は `current_app.instance_path/participants.db` を独立接続で読み、id/name/gender/level/activeだけをdictとして返す。通常のsqlite3のactive返却値は整数0/1であり、ORM objectやBooleanのJSON返却への置換はAPI互換性に影響し得る。SQLAlchemyの設定URIやsessionへ統一すると、参照先、未commitデータの可視性、autoflush、接続寿命も変わる。SQLAlchemyの列選択と明示的変換による置換は可能だが、これらの仕様確認・API回帰テストを伴う別作業とする。

D1等へ切り替える際には、raw sqlite3とSQLAlchemyの二重アクセス、app context/scoped session、ORM object/relationship依存、呼び出し元の書き込みtransaction、SQLite用schema互換SQL、組み合わせ・統計utility内の直接queryが残る。JSON stateとDBの整合性・unique制約・通知重複防止も移行時に再設計/検証が必要。今回はこれらの設計変更を含めない。

## 互換性と検証

- transaction boundary変更なし。commit/rollback/flush・bulk deleteの呼び出し順は保持。
- models.py、DB schema、JSON/config schema、route URL/HTTP method/endpoint、template/UIは変更なし。
- 組み合わせ、LINE通知、history dump、resetの仕様変更なし。マニュアル手順の更新は不要。
- `tests/conftest.py`: 既存モデルfakeを新しい参照元dataにも適用。assertionの削除・緩和なし。
- `tests/test_data_access.py`: 独立in-memory SQLiteで11件追加。active/card/ID集合、round番号とIDの最新判定、時刻/IDの順序、eager loading、LINE joinの各除外条件、inactive/他session/channel/重複通知の範囲、tokenとsession lookupを検証。
- 変更後 `pytest`: **298 passed in 8.01s**（既存287件＋追加11件）。
- `python -m compileall .`: 終了コード0。`git diff --check`: 成功。
- 既存queryをdata関数から展開してroute全体とsession utilityの変更前後ASTを比較し、import以外の処理一致を確認。
- 手動ブラウザ確認は未実施（UI変更なし）。既存の画面・route回帰テストは実施。
- commit / push / PR作成は未実施。commit前にはこの文書と差分の範囲、残存アクセスを確認する。

## 変更後 routes の直接DB呼び出し一覧

```text
routes/admin.py:80: db.session.add(...)
routes/admin.py:83: db.session.commit(...)
routes/admin.py:166: db.session.commit(...)
routes/admin.py:168: db.session.rollback(...)
routes/helpers.py:419: db.session.add(...)
routes/helpers.py:420: db.session.flush(...)
routes/helpers.py:432: db.session.add(...)
routes/helpers.py:438: db.session.commit(...)
routes/helpers.py:442: db.session.commit(...)
routes/helpers.py:444: db.session.rollback(...)
routes/helpers.py:483: db.session.commit(...)
routes/helpers.py:581: db.session.add(...)
routes/helpers.py:596: db.session.add(...)
routes/helpers.py:601: db.session.commit(...)
routes/helpers.py:733: MatchNotification.query.delete(...)
routes/helpers.py:734: NotificationDeliveryLog.query.delete(...)
routes/helpers.py:735: NotificationSubscription.query.delete(...)
routes/helpers.py:736: LineLinkToken.query.delete(...)
routes/helpers.py:737: LineAccount.query.delete(...)
routes/helpers.py:738: MatchSession.query.delete(...)
routes/helpers.py:739: BenchHistory.query.delete(...)
routes/helpers.py:740: MatchHistory.query.delete(...)
routes/helpers.py:741: MatchRound.query.delete(...)
routes/helpers.py:742: Participant.query.delete(...)
routes/helpers.py:1015: BenchHistory.query.delete(...)
routes/helpers.py:1016: MatchHistory.query.delete(...)
routes/helpers.py:1017: MatchRound.query.delete(...)
routes/helpers.py:1229: db.session.rollback(...)
routes/helpers.py:1238: db.session.rollback(...)
routes/helpers.py:1240: db.session.commit(...)
routes/history.py:78: db.session.commit(...)
routes/history.py:99: db.session.commit(...)
routes/history.py:135: db.session.commit(...)
routes/history.py:137: db.session.rollback(...)
routes/line.py:63: db.session.add(...)
routes/line.py:66: db.session.commit(...)
routes/line.py:76: db.session.add(...)
routes/line.py:77: db.session.commit(...)
routes/line.py:95: db.session.commit(...)
routes/match.py:330: db.session.add(...)
routes/match.py:331: db.session.flush(...)
routes/match.py:334: db.session.add(...)
routes/match.py:344: db.session.add(...)
routes/match.py:370: db.session.commit(...)
routes/match.py:372: db.session.rollback(...)
routes/match.py:416: BenchHistory.query.filter_by(...).delete(synchronize_session=False)
routes/match.py:417: MatchHistory.query.filter_by(...).delete(synchronize_session=False)
routes/match.py:418: db.session.delete(...)
routes/match.py:420: db.session.commit(...)
routes/participant.py:60: db.session.add(...)
routes/participant.py:61: db.session.commit(...)
routes/participant.py:122: db.session.commit(...)
```

## 変更ファイル一覧

新規:

- `data/__init__.py`
- `data/participants.py`
- `data/match_history.py`
- `data/match_sessions.py`
- `data/line_notifications.py`
- `tests/test_data_access.py`
- `docs/db-access-boundary.md`

更新:

- `routes/admin.py`
- `routes/helpers.py`
- `routes/history.py`
- `routes/match.py`
- `routes/participant.py`
- `utils/match_session.py`
- `tests/conftest.py`
