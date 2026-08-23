import Link from "next/link";
import type { ReactNode } from "react";
import { Sa3arlyBrand, Sa3arlyIcon } from "./brand-system";

export default function PublicShell({
  children,
  breadcrumbs = [],
}: {
  children: ReactNode;
  breadcrumbs?: { href?: string; label: string }[];
}) {
  return (
    <div className="public-site">
      <header className="compare-header">
        <div className="compare-container compare-header-inner">
          <Link className="compare-brand" href="/" aria-label="سعرلي — الصفحة الرئيسية">
            <Sa3arlyBrand />
          </Link>
          <nav aria-label="التنقل الرئيسي">
            <Link href="/">المقارنة</Link>
            <Link href="/categories">التصنيفات</Link>
            <Link href="/brands">الماركات</Link>
            <Link href="/stores">المتاجر</Link>
            <Link href="/cash">الكاش</Link>
            <Link href="/installments">التقسيط</Link>
          </nav>
          <Link className="compare-header-cta" href="/#selector">
            ابدأ المقارنة
            <Sa3arlyIcon name="arrow" />
          </Link>
        </div>
      </header>
      <main className="compare-container public-main">
        {breadcrumbs.length > 0 && (
          <nav className="public-breadcrumbs" aria-label="مسار التنقل">
            {breadcrumbs.map((item, index) => (
              <span key={`${item.label}-${index}`}>
                {item.href ? <Link href={item.href}>{item.label}</Link> : item.label}
                {index < breadcrumbs.length - 1 && (
                  <i aria-hidden="true">/</i>
                )}
              </span>
            ))}
          </nav>
        )}
        {children}
      </main>
      <footer className="compare-footer">
        <div className="compare-container">
          <div>
            <Sa3arlyBrand />
            <span>نرتب لك الصورة كاملة: السعر، الشحن، التوفر والضمان.</span>
          </div>
          <nav aria-label="روابط الموقع">
            <Link href="/methodology">المنهجية</Link>
            <Link href="/privacy">الخصوصية</Link>
            <Link href="/terms">الشروط</Link>
            <Link href="/contact">تواصل معنا</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
