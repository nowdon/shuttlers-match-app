# Changelog

すべての notable な変更はこのファイルに記録されます。  
このプロジェクトは [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) に従って記述されています。

## [v1.0.0] - 2025-06-24
### Added
- `requirements.txt` を追加
- `match_state.json` に `bench` 情報を保存する構造に変更
- `match_state.py` に `save_match_state_full()` を追加
- `/match/confirm` で確定時に `matchs`, `bench`, `match_count` を保存
- `/match/edit` 表示時に前回の待機者を識別して名前に * を追加


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