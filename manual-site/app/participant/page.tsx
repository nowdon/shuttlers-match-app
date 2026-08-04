import type { Metadata } from "next";
import { Icon } from "../components/icon";
import { ManualShell } from "../components/manual-shell";

export const metadata: Metadata = { title: "参加者向け" };

const quickLinks = [
  { href: "#registration", label: "初回登録" },
  { href: "#participation", label: "参加・休憩" },
  { href: "#payment", label: "参加費" },
  { href: "#line-notification", label: "LINE通知" },
  { href: "#card-and-match", label: "組み合わせ" },
];

export default function ParticipantPage() {
  return (
    <ManualShell>
      <section className="hero participant-hero">
        <p className="eyebrow">FOR PARTICIPANTS</p>
        <h1>参加者向け</h1>
        <p className="lead">
          最初に自分のカードを登録したら、同じカードから参加状態や組み合わせを確認できます。
          当日の流れに沿って操作を案内します。
        </p>
      </section>

      <nav className="page-toc" aria-label="参加者向けページ内メニュー">
        <p>知りたい項目へ</p>
        <div>
          {quickLinks.map((item) => (
            <a href={item.href} key={item.href}>
              {item.label}
            </a>
          ))}
        </div>
      </nav>

      <section className="manual-section" id="registration">
        <div className="manual-section-heading">
          <span className="manual-step-number">01</span>
          <div>
            <p className="section-kicker">初めて使うとき</p>
            <h2>空いているカードを選んで参加登録する</h2>
          </div>
        </div>

        <div className="instruction-grid">
          <ol className="instruction-list">
            <li>
              <strong>参加者一覧を開く</strong>
              <span>ハート・ダイヤ・クラブ・スペードのカードが並んでいます。</span>
            </li>
            <li>
              <strong>名前が入っていないカードをタップ</strong>
              <span>空いているカードの中から、好きなものを1枚選びます。</span>
            </li>
            <li>
              <strong>名前・性別・競技レベルを入力</strong>
              <span>競技レベルは「初級・中級・上級」から選びます。</span>
            </li>
            <li>
              <strong>「登録」をタップ</strong>
              <span>登録が終わると、参加費とLINE通知の案内が表示されます。</span>
            </li>
          </ol>

          <aside className="card-choice-note" aria-label="カード選びのおすすめ">
            <div className="callout-title">
              <Icon name="card" size={25} />
              <strong>カードは自由に選べます</strong>
            </div>
            <p>
              選ぶカードに決まりはありません。会場で見分けやすくしたい場合は、次の分け方がおすすめです。
            </p>
            <div className="suit-guide">
              <div>
                <span className="suit-symbol red-suit">♥ ♦</span>
                <strong>赤のカード</strong>
                <small>女性におすすめ</small>
              </div>
              <div>
                <span className="suit-symbol black-suit">♣ ♠</span>
                <strong>黒のカード</strong>
                <small>男性におすすめ</small>
              </div>
            </div>
            <p className="callout-footnote">
              ※ 色分けは必須ではありません。ジョーカーも空いていれば選べます。
            </p>
          </aside>
        </div>

        <div className="attention-note">
          <strong>カードにすでに名前がある場合</strong>
          <p>
            そのカードは登録済みです。初回登録では、名前が入っていない別のカードを選んでください。
          </p>
        </div>
      </section>

      <section className="manual-section" id="participation">
        <div className="manual-section-heading">
          <span className="manual-step-number">02</span>
          <div>
            <p className="section-kicker">登録したあと</p>
            <h2>参加・休憩・早退を切り替える</h2>
          </div>
        </div>

        <p className="section-intro">
          参加者一覧で自分のカードをタップすると、登録情報を変更できます。
        </p>
        <div className="state-cards">
          <article>
            <span className="state-check" aria-hidden="true">✓</span>
            <h3>ゲームに参加するとき</h3>
            <p>「ゲーム参加中」にチェックを入れて「更新」をタップします。</p>
          </article>
          <article>
            <span className="state-pause" aria-hidden="true">—</span>
            <h3>休憩・早退するとき</h3>
            <p>「ゲーム参加中」のチェックを外して「更新」をタップします。</p>
          </article>
        </div>
        <div className="tip-note">
          <Icon name="spark" size={23} />
          <p>
            休憩から戻るときは、同じ画面でチェックを入れ直してください。名前・性別・競技レベルもここから変更できます。
          </p>
        </div>
      </section>

      <section className="manual-section" id="payment">
        <div className="manual-section-heading">
          <span className="manual-step-number">03</span>
          <div>
            <p className="section-kicker">登録完了後</p>
            <h2>参加費を支払う</h2>
          </div>
        </div>

        <p className="section-intro">
          登録完了画面で、当日の案内に従って現金またはPayPayで支払います。
        </p>
        <div className="payment-options">
          <article>
            <span aria-hidden="true">💰</span>
            <div>
              <h3>現金</h3>
              <p>参加費を会場のかごに入れます。</p>
            </div>
          </article>
          <article>
            <span aria-hidden="true">📱</span>
            <div>
              <h3>PayPay</h3>
              <p>社会人・学生の該当する支払いボタンをタップします。</p>
            </div>
          </article>
        </div>
        <p className="muted-note">
          支払いが終わったら「支払い完了 → トップに戻る」をタップします。参加費は開催時の画面表示を確認してください。
        </p>
      </section>

      <section className="manual-section" id="line-notification">
        <div className="manual-section-heading">
          <span className="manual-step-number">04</span>
          <div>
            <p className="section-kicker">任意</p>
            <h2>LINEで組み合わせ通知を受け取る</h2>
          </div>
        </div>

        <p className="section-intro">
          LINE通知を登録すると、組み合わせが確定したときに自分のコート・ペア・対戦相手、または待機をLINEで確認できます。
        </p>
        <ol className="compact-steps">
          <li><span>1</span><p>登録完了画面で「LINE通知を登録する」をタップ</p></li>
          <li><span>2</span><p>表示された連携コードをタップしてコピー</p></li>
          <li><span>3</span><p>「LINEでBotを開く」からトーク画面を開く</p></li>
          <li><span>4</span><p>コピーしたコードを貼り付けて送信</p></li>
        </ol>
        <div className="attention-note line-note">
          <strong>次回参加時も確認してください</strong>
          <p>
            LINEアカウントの連携後も、当日分の通知登録が必要な場合があります。登録完了画面に「今回のLINE通知を受け取る」が表示されたらタップしてください。
          </p>
        </div>
      </section>

      <section className="manual-section" id="card-and-match">
        <div className="manual-section-heading">
          <span className="manual-step-number">05</span>
          <div>
            <p className="section-kicker">組み合わせ確定後</p>
            <h2>自分のコートと組み合わせを確認する</h2>
          </div>
        </div>

        <ol className="instruction-list single-column">
          <li>
            <strong>参加者一覧を更新する</strong>
            <span>「表示を更新」をタップして最新の状態にします。</span>
          </li>
          <li>
            <strong>「現在の組み合わせを表示」をタップ</strong>
            <span>組み合わせが確定しているときだけボタンが表示されます。</span>
          </li>
          <li>
            <strong>自分のカードを探す</strong>
            <span>コート番号、ペア、対戦相手を確認します。待機の場合は待機者欄に表示されます。</span>
          </li>
        </ol>
        <div className="tip-note">
          <Icon name="bell" size={23} />
          <p>
            LINE通知を登録している場合は、組み合わせ確定時に同じ内容が届きます。表示が古いときは、ページを更新してください。
          </p>
        </div>
      </section>
    </ManualShell>
  );
}
