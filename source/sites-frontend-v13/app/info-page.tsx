import type { ReactNode } from "react";
import Link from "next/link";
import { Sa3arlyBrand, Sa3arlyIcon } from "./brand-system";

export default function InfoPage({
  eyebrow,
  title,
  intro,
  children,
}: {
  eyebrow: string;
  title: string;
  intro: string;
  children: ReactNode;
}) {
  return (
    <main className="info-page">
      <header className="info-header">
        <div className="container header-content">
          <Link className="brand" href="/" aria-label="العودة إلى سعرلي">
            <Sa3arlyBrand />
          </Link>
          <Link className="info-back" href="/">
            العودة للمقارنة <Sa3arlyIcon name="arrow" />
          </Link>
        </div>
      </header>
      <article className="container info-card">
        <span className="kicker">{eyebrow}</span>
        <h1>{title}</h1>
        <p className="info-intro">{intro}</p>
        <div className="info-body">{children}</div>
      </article>
    </main>
  );
}
