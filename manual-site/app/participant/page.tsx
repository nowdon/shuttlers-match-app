import type { Metadata } from "next";
import { SectionPage } from "../components/section-page";

export const metadata: Metadata = { title: "参加者向け" };

export default function ParticipantPage() {
  return (
    <SectionPage
      eyebrow="FOR PARTICIPANTS"
      lead="練習への参加登録から、自分のカードと組み合わせの確認、LINE通知の受け取り方までを案内します。"
      title="参加者向け"
      topics={[
        {
          id: "registration",
          icon: "user",
          title: "参加登録",
          description:
            "名前・性別・競技レベルを入力し、当日の参加者として登録します。",
        },
        {
          id: "card-and-match",
          icon: "card",
          title: "カードと組み合わせ",
          description:
            "割り当てられたカードから、自分のコート・ペア・対戦相手を確認します。",
        },
        {
          id: "line-notification",
          icon: "bell",
          title: "LINE通知を受け取る",
          description:
            "LINEアカウントを連携し、組み合わせ確定時の通知を登録します。",
        },
        {
          id: "result",
          icon: "history",
          title: "結果を確認する",
          description:
            "確定した最新の組み合わせと、入力済みの試合結果を確認します。",
        },
      ]}
    />
  );
}
