# Changelog

すべての notable な変更はこのファイルに記録されます。  
このプロジェクトは [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) に従って記述されています。

## [v1.6.0] - 2026-07-09

### Added

- LINE Bot による通知登録機能を追加
- 参加者と LINE userId を紐付ける `LineAccount` を追加
- 組み合わせセッションを管理する `MatchSession` を追加
- セッション単位の通知登録を管理する `NotificationSubscription` を追加
- LINE連携コードを管理する `LineLinkToken` を追加
- LINE通知送信結果を記録する `NotificationDeliveryLog` を追加
- `session_id + match_count + channel` 単位で通知送信済み状態を管理する `MatchNotification` を追加
- `/notifications/line/start/<card>` を追加し、参加者が thanks ページから LINE通知登録を開始できるように追加
- `/line/webhook` を追加し、LINE Bot から送られた連携コードを処理できるように追加
- 組み合わせ確定時に、現在セッションで通知登録済みの参加者へ LINE Push 通知を送る機能を追加
- 参加者ごとに、自分のコート番号と同じコートのメンバーを通知する個人別LINE通知を追加
- ベンチ参加者への待機通知を追加
- LINE連携成功時に、参加費とPayPay支払いリンクを案内する返信を追加
- LINE連携コード画面に、コードコピーと LINE Bot 起動ボタンを追加
- `LINE_BOT_FRIEND_URL` 環境変数を追加
- LINE通知登録、Webhook、Push通知、個人別通知、二重送信防止に関する回帰テストを追加

### Changed

- 参加登録完了画面の導線を、PayPay支払い案内よりLINE通知登録を優先する構成へ変更
- LINE通知登録後にLINE上で支払い案内を受け取れるよう、支払い導線をLINE連携フローに統合
- 組み合わせ確定通知を全員共通メッセージから参加者ごとの個人別メッセージへ変更
- 通知登録をLINEアカウント単位ではなく、現在の `MatchSession` 単位で扱うように整理
- 同じ練習会セッション内でも `match_count` ごとに通知できるよう、送信済み判定を `MatchNotification` に分離
- `/admin/reset_db` 時に LINE通知関連テーブルも明示的に削除するように変更
- 通知送信前に pending 状態をDBへ保存し、LINE送信後に送信結果を更新する流れへ変更

### Fixed

- LINE連携済みでも現在セッション未登録の参加者に通知が送られないように修正
- 過去セッションの通知登録者に現在セッションの通知が送られないように修正
- inactive な参加者が通知登録やLINE連携コード発行を行える可能性を修正
- 同じ `session_id + match_count + channel` の通知が重複送信される可能性を修正
- 2回目以降の組み合わせ確定通知が、同じ `MatchSession` 内でも送信されるように修正
- 既存DBに `notification_delivery_logs.match_count` カラムがない場合に起動時互換マイグレーションで追加するように修正
- LINE Bot URLやPayPayリンクが未設定の場合でも画面表示や連携処理が失敗しないように修正
- 通知対象参加者が matches / bench のどちらにも見つからない場合でも、通知処理全体が失敗しないように修正

### Notes

- LINE通知を使うには `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、必要に応じて `LINE_BOT_FRIEND_URL` を設定してください。
- 本番運用では、LINE Developers の Webhook URL に `https://<domain>/line/webhook` を設定してください。
- 通知登録は現在の `MatchSession` 単位で管理されるため、過去の練習会参加者には現在の通知は送られません。
- PayPayリンクやLINEアクセストークンなどの環境固有値・secretは Git に commit しないでください。

## [v1.5.0] - 2026-07-04

### Added

- `/match/edit` にペア固定モードを追加
- 同じペアの2人を選択して swap 操作すると、そのペアを固定できる機能を追加
- 固定済みペアの2人を再度選択して swap 操作すると、固定を解除できる機能を追加
- 固定ペアの片方を他の出場者と swap した場合、固定ペアごと移動する機能を追加
- `/match/edit` に固定ペアの「固定」バッジ表示を追加
- `/match/edit` に「スコアが近いペアで組み直す」ボタンを追加
- fixed_pairs を維持したまま、bench を変更せずに出場者ペアと対戦を再調整する機能を追加
- fixed_pairs 以外の出場者をランダムにペア化する機能を追加
- 現役DB上の `MatchHistory` を参照し、過去に組んだことのあるペアをできる限り避ける機能を追加
- 完成したペア同士を `pair_score` が近い対戦になるように並べる機能を追加
- `draft_state.json` に `fixed_pairs` を保存できるように追加
- 連続出場ベンチ優先回数 `consecutive_play_limit` を config に追加
- `/admin/settings` から `consecutive_play_limit` を変更できる機能を追加
- fixed_pairs や legacy draft に関する回帰テストを追加
- `/match/optimize_pairs` の admin-only 制御テストを追加
- malformed fixed_pairs に対する防御テストを追加

### Changed

- 組み合わせ生成時の連続出場判定回数を固定値から `consecutive_play_limit` 設定値へ変更
- 連続出場者の検出処理を、設定値に応じた判定へ変更
- `/match/edit` の swap 処理を fixed_pairs を考慮する処理へ拡張
- fixed_pairs がある場合の swap は、ペア単位で移動するように変更
- 固定ペアの片方と bench 参加者の個別 swap は拒否するように変更
- 「スコアが近いペアで組み直す」処理では、ペア作成時に player_score / pair_score で均等化せず、スコアは完成ペア同士の対戦調整段階でのみ使うように変更
- ペア調整ロジックを `app.py` から `utils/pair_optimizer.py` に切り出し
- `/match/optimize_pairs` のルート処理を薄くし、最適化ロジックを utility 側へ分離
- `/match/edit`、`/match/confirm`、`/match/swap`、`/match/optimize_pairs` で legacy draft の trailing short group を安全に扱うように変更
- fixed_pairs がない legacy short draft では、trailing short group を bench 相当として扱うように変更
- `/match/swap` と `/match/optimize_pairs` で legacy short draft を保存する際、matches を4人単位、bench を bench 配列へ正規化するように変更
- README を v1.5.0 向けに更新

### Fixed

- fixed_pairs が malformed な場合に `/match/edit` が500になる可能性を修正
- malformed fixed_pairs が `/match/optimize_pairs` 経由で `[]` や `[[1, 2]]` に勝手に正規化保存される可能性を修正
- malformed fixed_pairs が `/match/swap` 経由で勝手に正規化保存される可能性を修正
- fixed_pairs のIDとして float、numeric string、bool などが受け入れられる可能性を修正
- fixed_pairs がある short trailing group draft を `/match/edit`、`/match/confirm`、`/match/swap`、`/match/optimize_pairs` で安全に拒否するように修正
- `/match/optimize_pairs` が viewer mode や mode 省略のPOSTでも実行できてしまう問題を修正
- `/match/optimize_pairs` の failure 後に壊れた draft の `/match/edit` へ戻って500になる可能性を修正
- `/match/edit` で flash message が表示されず、固定ペアと bench 参加者の swap 拒否理由が見えない問題を修正
- fixed_pairs 付き legacy short draft が `/match/swap` 経由で新規作成される可能性を修正
- fixed_pairs がない legacy short draft を `/match/optimize_pairs` で誤って壊れた draft 扱いする問題を修正
- 不正な draft_state.json でテンプレート描画前に安全にリダイレクトするように修正

## [v1.4.1] - 2026-06-30

### Added

- 管理画面トップから設定画面へ移動できる「設定」ボタンを追加
- 画面遷移リンクと主要操作ボタン向けの共通ボタンスタイルを追加

### Changed

- 管理画面、組み合わせ結果、組み合わせ編集、履歴、CSVアップロード、登録・編集フォームの主要リンクとボタンの見た目を統一
- 主要操作、補助操作、危険操作のボタン表示を `btn` / `btn-secondary` / `btn-danger` に整理
- 管理画面トップの組み合わせ関連ボタンを、確定済み組み合わせと編集中組み合わせの有無に応じた配置へ変更
- 管理画面トップの「設定」「試合履歴」「参加者情報をCSVで一括登録」を画面下部の導線として整理
- CSVアップロード画面の戻る導線をページ上部へ移動し、表示文言を「管理画面に戻る」に変更
- 参加者一覧から参加費支払い案内とQRコード表示を削除
- 参加者一覧テーブルを、モバイル表示時に表部分だけ横スクロールできる表示へ変更
- モバイル表示時に各画面のボタンが画面幅いっぱいに表示されるように変更

### Fixed

- 組み合わせ結果画面で「このラウンドの結果を保存」ボタンが横スクロール領域に含まれる問題を修正
- 危険操作ボタンと補助操作ボタンの見た目が区別しづらい問題を修正

## [v1.4.0] - 2026-06-30

### Added

- 試合履歴をDBに保存する機能を追加
- 組み合わせ確定時に MatchRound / MatchHistory / BenchHistory としてラウンド・コート別対戦・ベンチ情報を保存するように追加
- 管理者向けに /admin/match_history を追加し、現在DBに残っている試合履歴を確認できるように追加
- 試合履歴ページで参加者名と割り当てカードを表示する機能を追加
- 勝敗のみを入力する winner_only モードを追加
- ゲームごとのスコアを入力する score モードを追加
- score モードで、ゲームカウントから勝敗を自動判定する機能を追加
- 管理者設定にスコア入力モード、ポイント数、ゲーム数、デュース有無、最大ポイント数の設定を追加
- /admin/match_history でラウンド単位に複数コートの試合結果をまとめて保存できる機能を追加
- adminモードの /match/result から試合結果を入力・保存できる機能を追加
- 試合履歴をJSONにダンプする機能を追加
- 試合履歴をJSONにダンプしたうえでDB上の履歴を消去する機能を追加
- 全データ削除時に、削除前の試合履歴をJSONへ自動ダンプする機能を追加
- ダンプ済み履歴JSONを一覧表示する /admin/match_history_archives を追加
- ダンプ済み履歴JSONの詳細を表示する /admin/match_history_archives/<filename> を追加
- 入力済み試合結果から参加者ごとの勝率を集計する機能を追加
- プレイヤースコアに勝率補正を加える機能を追加
- 試合履歴、結果入力、履歴ダンプ、ダンプ済み履歴表示、勝率補正に関する回帰テストを追加

### Changed

- /admin/match_history を現在DBに残っている現役履歴の管理ページとして整理
- ダンプ済み履歴は /admin/match_history_archives で参照専用として表示するように変更
- 試合結果の保存を、1コートごとの保存からラウンド単位の一括保存へ変更
- /admin/match_history の表示を1コートごとに縦に並べるレイアウトへ変更
- adminモードの /match/result では結果入力UIを表示し、viewerモードでは表示しないように変更
- score モードの入力UIを、複数行テキスト入力からゲームごとのドロップダウン入力へ変更
- プレイヤースコア計算を level_score * weight から level_score * weight + win_rate へ変更
- 勝率集計は現在DBに残っている MatchHistory のみを対象とし、ダンプ済みJSONは対象外に変更
- instance/history_dumps/ 配下の履歴ダンプJSONをGit管理対象外に変更
- READMEをv1.4.0の機能内容に合わせて更新

### Fixed

- 全データ削除時に履歴ダンプへ失敗した場合、削除処理を中止するように修正
- 履歴ダンプJSONが誤ってGit管理対象になる可能性を修正
- 履歴消去時に Participant や games_played が変更されないように修正
- ラウンド単位の結果一括保存で、1コートでも不正入力がある場合に一部だけ保存される可能性を修正
- winner_only モードの一括保存で、不正な winner_team 値が未入力扱いで保存される可能性を修正
- ダンプ済み履歴詳細でパストラバーサルにより history_dumps 外のファイルを読める可能性を修正
- 壊れたJSONや想定外構造のダンプ済み履歴JSONで一覧・詳細ページが500になる可能性を修正
- ダンプ済み履歴の詳細URL /admin/match_history_archives/<filename> が利用できない問題を修正


## [v1.3.0] - 2026-06-23
### Added
- 確定済み組み合わせを「次の組み合わせ（編集中）」へ戻して再編集できる機能を追加
- 管理者向けに「確定を取り消して編集に戻す」ボタンを追加
- `/match/draft` を追加し、次の組み合わせ（編集中）を表示できるように変更
- `/match/result` を追加し、確定済み組み合わせを表示できるように変更
- 旧URL `/match_result` から `/match/result` へのリダイレクトを追加
- 確定済み組み合わせ、次の組み合わせ（編集中）、編集画面の表示・権限に関する回帰テストを追加
- 確定取り消し時の `games_played` / `match_count` 巻き戻しに関する回帰テストを追加
### Changed
- 確定済み組み合わせは `/match/result`、次の組み合わせ（編集中）は `/match/draft`、編集画面は `/match/edit` で扱うように表示ルートを整理
- 表示文言を「仮組み合わせ」から「次の組み合わせ（編集中）」へ変更
- viewer は次の組み合わせ（編集中）を表示できるが、編集画面には遷移できないように変更
- admin は確定済み組み合わせ画面から編集画面へ直接遷移できるように変更
- active draft が存在する場合は「もう一度組み合わせを生成」ボタンを表示しないように変更
- 確定を取り消した場合、確定時に加算された参加者の試合数と `match_count` を巻き戻すように変更
### Fixed
- 確定済み組み合わせと編集中の組み合わせが同時に存在する場合に、表示対象が分かりにくくなる問題を修正
- viewer が `/match/edit` に直接アクセスした場合に編集画面へ到達できる問題を修正
- 確定後に組み合わせを修正したい場合、試合数が二重カウントされる可能性を修正
- active draft が存在する状態で再生成ボタンが表示され、編集中の組み合わせを上書きしてしまう可能性を修正


## [v1.2.0] - 2026-06-20
### Added
- Flask session 利用方針を整理し、業務状態を session に保存しない方針を明確化
- `SECRET_KEY` を環境変数から設定する仕組みを追加
- ローカル開発用に `ALLOW_DEV_SECRET_KEY=1` による明示的な開発用 fallback を追加
- flash メッセージ表示を共通テンプレート化
- 管理画面、設定画面、組み合わせ生成画面で flash メッセージを表示
- `match_state.json` に確定済み試合のメタ情報として `court_count` を保存
- 確定済み試合の `court_count` を使って、次回の「もう一度組み合わせを生成」で前回コート数を復元
- 状態管理、SECRET_KEY、flash 表示、court_count 復元に関する回帰テストを追加
### Changed
- 未確定の組み合わせ状態を `draft_state.json` に統一
- 確定済みの組み合わせ状態を `match_state.json` に統一
- `draft_matches` / `draft_bench` / `court_count` / `last_confirmed_-` を Flask session から削除
- `/match/edit` と `/match/confirm` が `draft_state.json` を正本として扱うように変更
- `/match/swap` とコート数変更処理が `draft_state.json` を直接更新するように変更
- `court_count` の旧 schema 互換として、保存値がない場合は確定済み `matches` の件数から推定するように変更
- `requirements.txt` と README の開発手順を整理
### Fixed
- 複数ブラウザ・複数 worker で Flask session の業務状態がずれる可能性を解消
- `draft_state.json` の `matches` が typo により空で上書きされる可能性を修正
- `/match/edit` 直接アクセス時に共有 draft が空で上書きされる問題を修正
- 確定処理時に古い session draft が使われる可能性を修正
- コート数変更や swap 後に `court_count` が失われる問題を修正
- 組み合わせ確定後に「もう一度組み合わせを生成」するとコート数指定画面に戻ってしまう問題を修正
- 再生成 draft 作成時に `match_state.json` の `court_count` が消える問題を修正
- 本番環境で `SECRET_KEY` 未設定のまま開発用固定キーにフォールバックする問題を修正
- 表示されていなかった管理系 flash メッセージを表示できるように修正
### Notes
- 本番環境では `SECRET_KEY` の設定が必須
- ローカル開発では `SECRET_KEY` を設定するか、開発用途に限り `ALLOW_DEV_SECRET_KEY=1` を設定
- Flask session は flash 通知用途のみ使用し、業務状態は JSON state / DB を正本とする

## [v1.1.0] - 2025-10-16
### Added
- `models.py` を追加し、クラスの定義を切り出し
- `reset.py` を追加し、参加者及びマッチ状態のリセット処理を共通化
- `match_state.py` に残っていた古い `save_match_state()` を削除して処理を `save_match_state_full()` に一本化

## [v1.0.0] - 2025-06-24
### Added
- `requirements.txt` を追加
- `match_state.json` に `bench` 情報を保存する構造に変更
- `match_state.py` に `save_match_state_full()` を追加
- `/match/confirm` で確定時に `matchs`, `bench`, `match_count` を保存
- `/match/edit` 表示時に前回の待機者を識別して名前に - を追加


## [Unreleased] - 2025-05-29
### Added
- `utils/score.py` を追加し、プレイヤーの競技レベルとgender_weightからペアの合計スコアを計算可能に
- `match_edhit.html` にプレイヤー及びペアのスコアを表示（adminモードのみ）


## [Unreleased] - 2025-05-15
### Added
- `GET /api/participants`, `GET /api/match_state` API を追加し、参加者情報とmatch_state情報をJSON形式で取得可能に
- `index.html`, `match_result.html` に手動更新ボタン（🔄）を追加し、ユーザーがページを再読み込みできるようにした


## [v0.9.0] - 2025-05-15
### Added
- 参加者登録機能（名前・性別・レベル）
- ダブルス組み合わせ生成機能（性別・レベル・試合回数考慮）
- 試合回数の記録と偏り抑制ロジック
- 仮組み合わせ保存と編集（draft_state.json）
- 組み合わせ確定による試合回数の反映
- 参加費支払い案内ページ（thanks.html、PayPay対応）
- 設定ファイル管理（config.json）：料金、PayPayリンク、LEVEL/GENDER設定
- 管理者向けページ（admin.html）：データ初期化、設定変更
- スマートフォン表示対応（register/thanks ページ）
- 状態ファイル（match_state.json, draft_state.json）を `.gitignore` に追加
