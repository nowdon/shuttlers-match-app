# Changelog

すべての notable な変更はこのファイルに記録されます。  
このプロジェクトは [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) に従って記述されています。

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