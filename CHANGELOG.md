# Changelog

すべての notable な変更はこのファイルに記録されます。  
このプロジェクトは [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) に従って記述されています。

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