import type { Metadata } from "next";
import Link from "next/link";
import PublicShell from "../public-shell";
import { safeJsonLd } from "../product-schema";
import {
  absoluteUrl,
  categoryEntries,
  categoryPath,
  DATA_LAST_MODIFIED,
  topCategoryEntries,
  topCategoryPath,
} from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "تصنيفات وأسعار المنتجات في مصر | سعرلي" },
  description:
    "تصفح تصنيفات المنتجات العامة المتاحة على سعرلي، ثم قارن المواصفات وأسعار الكاش والتقسيط بين متاجر مصر.",
  alternates: { canonical: absoluteUrl("/categories") },
  robots: { index: true, follow: true },
  openGraph: {
    type: "website",
    url: absoluteUrl("/categories"),
    title: "تصنيفات وأسعار المنتجات في مصر | سعرلي",
    description:
      "تصفح تصنيفات المنتجات العامة المتاحة على سعرلي وقارن المتاجر.",
  },
};

export default function CategoriesPage() {
  const types = categoryEntries.filter((entry) => entry.kind === "type");
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "سعرلي",
        item: absoluteUrl("/"),
      },
      {
        "@type": "ListItem",
        position: 2,
        name: "التصنيفات",
        item: absoluteUrl("/categories"),
      },
    ],
  };
  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { label: "التصنيفات" },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>دليل التصنيفات</span>
        <h1>تصنيفات وأسعار المنتجات في مصر</h1>
        <p>
          اختر القطاع أو نوع المنتج للوصول إلى موديلات حقيقية وروابط منتجات
          قابلة للزحف ومقارنة المتاجر التي ثبت لديها نفس النسخة.
        </p>
        <small>آخر تحديث للبيانات: {new Date(DATA_LAST_MODIFIED).toLocaleString("ar-EG")}</small>
      </header>

      <section className="seo-directory-section">
        <h2>الأقسام الرئيسية</h2>
        <div className="seo-directory-grid">
          {topCategoryEntries.map((entry) => (
            <Link href={topCategoryPath(entry)} key={entry.slug}>
              <b>{entry.name}</b>
              <span>
                {entry.allProducts.length.toLocaleString("ar-EG-u-nu-latn")} منتج
              </span>
            </Link>
          ))}
        </div>
      </section>

      <section className="seo-directory-section">
        <h2>أنواع المنتجات</h2>
        <div className="seo-directory-grid compact">
          {types.map((entry) => (
            <Link href={categoryPath(entry)} key={entry.slug}>
              <b>{entry.name}</b>
              <span>
                {entry.products.length.toLocaleString("ar-EG-u-nu-latn")} منتج
              </span>
            </Link>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
