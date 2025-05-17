# Shuttlers Match App 🏸

バドミントンイベントの参加者管理とダブルス組み合わせを自動化する Flask ベースの Web アプリケーションです。

## 🔍 概要

- 参加者登録（名前、性別、競技レベル）
- 公平性を考慮したダブルス組み合わせ生成
- コート数の管理と待機者の割り当て
- 試合回数の偏り最小化
- 参加費案内とQRコード支払い
- 管理者ビューで設定や状態の操作

## 🛠 使用技術

- Python 3.x
- Flask
- SQLite
- Bootstrap (Flaskテンプレート内)
- JavaScript (一部動的UI)
- JSON（設定ファイル・状態管理）

## 🚀 セットアップ方法

```bash
git clone https://github.com/nowdon/shuttlers-match-app.git
cd shuttlers-match-app
python -m venv venv
source venv/bin/activate  # Windows の場合は venv\Scripts\activate
pip install -r requirements.txt
```

## ▶️ 起動方法
```bash
python app.py
```

http://localhost:5001 でアクセスできるようになります。
(ポートの変更はapp.pyの最終行で指定してください。)

## 🗂 ディレクトリ構成（例）
```
shuttlers-match-app/
├── app.py
├── logic.py
├── instance/
│   └── participants.db
├── templates/
│   ├── admin_settings.html
│   ├── index.html
│   ├── match_edit.html
│   ├── match_form.html
│   ├── match_result.html
│   ├── participant_edit.html
│   ├── register.html
│   ├── thanks.html
│   └── upload_csv.html
├── static/
│   ├── participants_template.csv
│   └── cards/
│       └── ※カード画像を配置（詳細は下記）
├── utils/
│   ├── draft_state.py
│   ├── match_state.py
│   └── match_io.py
├── config.json
├── match_state.json
├── draft_state.json
├── CHANGELOG.md
└── README.md
```

## 🃏 カード画像について

本アプリではトランプの絵柄画像を使用しています。以下の画像を **`static/cards` フォルダ** に各自で用意・配置してください。

### 🎴 ファイル名ルール

- ♣ クラブ: `cA.png`, `c2.png`, ..., `cJ.png`, `cQ.png`, `cK.png`
- ♠ スペード: `sA.png`, `s2.png`, ..., `sJ.png`, `sQ.png`, `sK.png`
- ♦ ダイヤ: `dA.png`, `d2.png`, ..., `dJ.png`, `dQ.png`, `dK.png`
- ♥ ハート: `hA.png`, `h2.png`, ..., `hJ.png`, `hQ.png`, `hK.png`
- 🃏 ジョーカー: `joker_black.png`, `joker_red.png`

※ 画像サイズは統一されていることが望ましいです。

## 🔐モードについて

本アプリには一般参加者向けのviewerモードと管理者向けのadminモードがあります。

### viewerモード

http://localhost:5001 でアクセス

- 参加者の登録が可能
- 参加者情報の修正が可能
- 試合の組み合わせの閲覧が可能

### adminモード

http://localhost:5001/admin でアクセス

- 参加者の登録が可能
- 参加者情報の修正が可能
- 組み合わせの生成が可能
- 組み合わせセッションの生成・リセットが可能
- ブラウザ上でconfig.jsonの修正が可能(http://localhost:5001/admin/settings でアクセス)
- 参加者dbの内容を全削除が可能

## 📄 ライセンス

MIT License

## 👤 作者

[@nowdon](https://github.com/nowdon)