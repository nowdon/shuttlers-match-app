# Shuttlers Match App 操作マニュアル

Shuttlers Match App の参加者・管理者向け操作マニュアルサイトです。
アプリ本体と同じリポジトリで管理し、リリース時に同じバージョンを案内します。

## 構成

- `/`: マニュアルトップ
- `/participant`: 参加者向け
- `/admin`: 管理者向け
- `/line`: LINE通知
- `/troubleshooting`: トラブルシューティング

共通ナビゲーション、サイト内検索、PC・スマートフォン向けのレスポンシブ表示を
`app/` 配下で管理しています。

## ローカル確認

Node.js 22.13 以降を使用します。

```bash
cd manual-site
npm ci
npm run dev
```

## 検証

```bash
npm run lint
npm test
```

`npm test` は Sites 向けの本番成果物を生成し、各ページの応答に加えて、
検索・ページ遷移・スマートフォン用メニューを実ブラウザで確認します。

## 公開

`.openai/hosting.json` は公開先となる Sites プロジェクトを識別します。
本番公開は `main` へ取り込んだ後、その時点の `manual-site/` を使用して行います。
