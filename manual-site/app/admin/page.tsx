import type { Metadata } from "next";
import { SectionPage } from "../components/section-page";

export const metadata: Metadata = { title: "管理者向け" };

export default function AdminPage() {
  return (
    <SectionPage
      eyebrow="FOR ADMINISTRATORS"
      lead="参加状況の確認、組み合わせの生成と調整、試合結果・履歴・各種設定の管理方法を案内します。"
      title="管理者向け"
      topics={[
        {
          id: "matching",
          icon: "spark",
          title: "組み合わせを作る",
          description:
            "参加者とコート数を確認し、公平性を考慮した仮組み合わせを生成・調整します。",
        },
        {
          id: "result",
          icon: "clipboard",
          title: "確定と結果入力",
          description:
            "仮組み合わせを確定し、勝敗またはゲームごとのスコアを登録します。",
        },
        {
          id: "history",
          icon: "history",
          title: "履歴管理",
          description:
            "過去の対戦とベンチ履歴を確認し、必要に応じてJSONで保存します。",
        },
        {
          id: "settings",
          icon: "settings",
          title: "管理者設定",
          description:
            "コート数、レベル、スコア方式、PayPayリンクなどを管理します。",
        },
      ]}
    />
  );
}
