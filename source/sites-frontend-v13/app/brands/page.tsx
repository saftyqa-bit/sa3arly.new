import type { Metadata } from "next";
import Link from "next/link";
import PublicShell from "../public-shell";
import { safeJsonLd } from "../product-schema";
import { absoluteUrl, brandEntries, brandPath } from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "ماركات المنتجات في مصر ومقارنة الأسعار | سعرلي" },
  description:
    "تصفح الماركات التي لديها منتجات فعلية في قاعدة سعرلي وقارن الموديلات والمواصفات والكاش والتقسيط بين المتاجر المصرية.",
  alternates: { canonical: absoluteUrl("/brands") },
  robots: { index: true, follow: true },
};

export default function BrandsPage() {
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
        name: "الماركات",
        item: absoluteUrl("/brands"),
      },
    ],
  };
  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { label: "الماركات" },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>دليل الماركات</span>
        <h1>ماركات المنتجات المتاحة على سعرلي</h1>
        <p>
          لا ننشر صفحة ماركة فارغة؛ كل رابط أدناه يقود إلى موديلات فعلية
          ومواصفات وروابط مقارنة منتجات قابلة للزحف.
        </p>
      </header>
      <section className="seo-directory-section">
        <div className="seo-directory-grid compact">
          {brandEntries.map((brand) => (
            <Link href={brandPath(brand)} key={brand.slug}>
              <b>
                <bdi dir="auto">{brand.name}</bdi>
              </b>
              <span>
                {brand.products.length.toLocaleString("ar-EG-u-nu-latn")} منتج
              </span>
            </Link>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
