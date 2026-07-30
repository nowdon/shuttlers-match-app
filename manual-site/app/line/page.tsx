import type { Metadata } from "next";
import { SectionPage } from "../components/section-page";

export const metadata: Metadata = { title: "LINE通知" };

export default function LinePage() {
  return (
    <SectionPage
      eyebrow="LINE NOTIFICATIONS"
      lead="LINEアカウントの連携から通知登録、組み合わせ確定時に届くメッセージまでの流れを案内します。"
      title="LINE通知"
      topics={[
        {
          id: "connect",
          icon: "link",
          title: "LINEを連携する",
          description:
            "参加登録後に表示されるリンクコードを、クラブのLINE Botへ送信します。",
        },
        {
          id: "subscribe",
          icon: "bell",
          title: "当日の通知を登録する",
          description:
            "現在の組み合わせセッションに通知登録し、確定を待ちます。",
        },
        {
          id: "message",
          icon: "message",
          title: "通知を確認する",
          description:
            "コート番号、ペア、対戦相手、またはベンチ待機の案内が個別に届きます。",
        },
        {
          id: "trouble",
          icon: "help",
          title: "通知が届かないとき",
          description:
            "連携状態、当日の通知登録、参加者の有効状態を順に確認します。",
        },
      ]}
    />
  );
}
