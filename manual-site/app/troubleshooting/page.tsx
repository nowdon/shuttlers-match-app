import type { Metadata } from "next";
import { SectionPage } from "../components/section-page";

export const metadata: Metadata = { title: "トラブルシューティング" };

export default function TroubleshootingPage() {
  return (
    <SectionPage
      eyebrow="TROUBLESHOOTING"
      lead="表示や操作に困ったときに、まず確認する項目をまとめています。"
      title="トラブルシューティング"
      topics={[
        {
          id: "not-found",
          icon: "user",
          title: "自分の名前が見つからない",
          description:
            "参加登録が完了しているか、当日の参加者として有効になっているかを確認します。",
        },
        {
          id: "old-match",
          icon: "history",
          title: "古い組み合わせが表示される",
          description:
            "画面を再読み込みし、確定済みと仮組み合わせの表示を確認します。",
        },
        {
          id: "line",
          icon: "message",
          title: "LINE通知が届かない",
          description:
            "LINE連携と現在のセッションへの通知登録が完了しているか確認します。",
        },
        {
          id: "admin",
          icon: "admin",
          title: "管理画面を操作できない",
          description:
            "管理者としてログインしているか、操作対象のセッションが有効かを確認します。",
        },
      ]}
    />
  );
}
