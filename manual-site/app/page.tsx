import Link from "next/link";
import { Icon } from "./components/icon";
import { ManualShell } from "./components/manual-shell";

const audienceCards = [
  {
    href: "/participant",
    icon: "racket" as const,
    title: "参加者向け",
    description: "参加登録・カード確認・LINE通知",
  },
  {
    href: "/admin",
    icon: "clipboard" as const,
    title: "管理者向け",
    description: "組み合わせ作成・結果入力・履歴管理",
  },
];

export default function Home() {
  return (
    <ManualShell>
      <section className="hero">
        <p className="eyebrow">SHUTTLERS MATCH APP MANUAL</p>
        <h1>はじめに</h1>
        <p className="lead">
          参加登録から組み合わせ確認、管理者の設定まで、操作方法を案内します。
        </p>
      </section>

      <section className="audience-grid" aria-label="利用者別メニュー">
        {audienceCards.map((card) => (
          <Link className="audience-card" href={card.href} key={card.href}>
            <span className="audience-icon" aria-hidden="true">
              <Icon name={card.icon} size={52} />
            </span>
            <span className="audience-card-copy">
              <strong>{card.title}</strong>
              <span>{card.description}</span>
            </span>
            <span className="card-arrow" aria-hidden="true">
              <Icon name="arrow" size={28} />
            </span>
          </Link>
        ))}
      </section>

      <section className="reading-section">
        <div className="section-heading">
          <h2>最初に読む</h2>
        </div>
        <article className="notice-card">
          <div className="notice-number">1</div>
          <div>
            <h3>あなたに合った入口を選びます</h3>
            <p>
              練習に参加する方は「参加者向け」、組み合わせを作る方は「管理者向け」から始めてください。
              LINE通知だけを確認したい場合は、左のメニューから直接開けます。
            </p>
          </div>
        </article>
      </section>
    </ManualShell>
  );
}
