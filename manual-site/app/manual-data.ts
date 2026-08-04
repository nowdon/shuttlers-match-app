import type { IconName } from "./components/icon";

export const navItems: Array<{
  href: string;
  label: string;
  icon: IconName;
}> = [
  { href: "/", label: "はじめに", icon: "book" },
  { href: "/participant", label: "参加者向け", icon: "user" },
  { href: "/admin", label: "管理者向け", icon: "admin" },
  { href: "/line", label: "LINE通知", icon: "message" },
  { href: "/troubleshooting", label: "トラブルシューティング", icon: "help" },
];

export const searchItems = [
  {
    href: "/",
    title: "はじめに",
    description: "マニュアルの使い方と利用者別の入口",
    keywords: "トップ 初めて 概要",
  },
  {
    href: "/participant#registration",
    title: "参加登録",
    description: "空いているカードを選び、名前・性別・レベルを登録する",
    keywords: "参加者 新規 登録 カード 赤 黒 ハート ダイヤ クラブ スペード",
  },
  {
    href: "/participant#participation",
    title: "参加・休憩・早退を切り替える",
    description: "ゲーム参加中のチェックを変更する",
    keywords: "参加者 休憩 早退 再開 active",
  },
  {
    href: "/participant#payment",
    title: "参加費を支払う",
    description: "現金またはPayPayで参加費を支払う",
    keywords: "参加者 支払い 参加費 現金 PayPay 社会人 学生",
  },
  {
    href: "/participant#card-and-match",
    title: "現在の組み合わせを確認する",
    description: "自分のコート、ペア、対戦相手、待機を確認する",
    keywords: "カード コート 試合 対戦 ペア 待機 ベンチ 更新",
  },
  {
    href: "/participant#line-notification",
    title: "LINE通知を受け取る",
    description: "LINEアカウントを連携して通知を登録する",
    keywords: "LINE 通知 連携",
  },
  {
    href: "/admin#matching",
    title: "組み合わせを作る",
    description: "参加者とコート数から仮組み合わせを生成する",
    keywords: "管理者 マッチ 組合せ 生成 ペア",
  },
  {
    href: "/admin#result",
    title: "確定と結果入力",
    description: "組み合わせの確定と試合結果の登録",
    keywords: "管理者 確定 スコア 勝敗",
  },
  {
    href: "/admin#history",
    title: "履歴管理",
    description: "試合履歴の確認とJSONダンプ",
    keywords: "管理者 履歴 ダンプ JSON",
  },
  {
    href: "/admin#settings",
    title: "管理者設定",
    description: "コート数、スコア、PayPayなどの設定",
    keywords: "管理者 設定 PayPay コート",
  },
  {
    href: "/line",
    title: "LINE通知",
    description: "連携から組み合わせ通知までの流れ",
    keywords: "LINE Bot 通知 登録 コード",
  },
  {
    href: "/troubleshooting",
    title: "トラブルシューティング",
    description: "操作に困ったときの確認事項",
    keywords: "問題 エラー 困った 表示されない",
  },
];
