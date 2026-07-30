import { Icon } from "./icon";
import type { IconName } from "./icon";
import { ManualShell } from "./manual-shell";

type Topic = {
  id: string;
  icon: IconName;
  title: string;
  description: string;
};

type SectionPageProps = {
  eyebrow: string;
  title: string;
  lead: string;
  topics: Topic[];
};

export function SectionPage({
  eyebrow,
  title,
  lead,
  topics,
}: SectionPageProps) {
  return (
    <ManualShell>
      <section className="hero">
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="lead">{lead}</p>
      </section>

      <section className="topic-grid" aria-label={`${title}の項目`}>
        {topics.map((topic) => (
          <article className="topic-card" id={topic.id} key={topic.id}>
            <span className="topic-card-icon" aria-hidden="true">
              <Icon name={topic.icon} />
            </span>
            <h2>{topic.title}</h2>
            <p>{topic.description}</p>
          </article>
        ))}
      </section>

      <div className="foundation-note">
        <Icon name="book" />
        <p>
          <strong>基盤作成中：</strong>{" "}
          このページには、次のPRで実際の操作手順と画面画像を追加します。
        </p>
      </div>
    </ManualShell>
  );
}
