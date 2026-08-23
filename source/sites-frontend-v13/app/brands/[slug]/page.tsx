import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ProductLinks from "../../product-links";
import PublicShell from "../../public-shell";
import { safeJsonLd } from "../../product-schema";
import {
  absoluteUrl,
  brandPath,
  cleanText,
  getBrandBySlug,
  OG_IMAGE_PATH,
  topCategoryEntries,
  topCategoryPath,
} from "../../seo-data";

type BrandPageProps = { params: Promise<{ slug: string }> };

export async function generateMetadata({
  params,
}: BrandPageProps): Promise<Metadata> {
  const { slug } = await params;
  const brand = getBrandBySlug(slug);
  if (!brand || !brand.products.length) {
    return {
      title: { absolute: "الماركة غير متاحة | سعرلي" },
      robots: { index: false, follow: false },
    };
  }
  const name = cleanText(brand.name, 100);
  const title = `أسعار منتجات ${name} في مصر ومقارنة المتاجر | سعرلي`;
  const description = `تصفح موديلات ${name} وقارن المواصفات والأسعار وخيارات الكاش والتقسيط بين المتاجر المصرية.`;
  const canonical = absoluteUrl(brandPath(brand));
  return {
    title: { absolute: title },
    description,
    alternates: { canonical },
    robots: { index: true, follow: true },
    openGraph: {
      type: "website",
      siteName: "سعرلي",
      locale: "ar_EG",
      title,
      description,
      url: canonical,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
  };
}

export default async function BrandPage({ params }: BrandPageProps) {
  const { slug } = await params;
  const brand = getBrandBySlug(slug);
  if (!brand || !brand.products.length) notFound();
  const categories = topCategoryEntries.filter((entry) =>
    entry.products.some((product) =>
      brand.products.some((item) => item.id === product.id),
    ),
  );
  const canonical = absoluteUrl(brandPath(brand));
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "سعرلي", item: absoluteUrl("/") },
      {
        "@type": "ListItem",
        position: 2,
        name: "الماركات",
        item: absoluteUrl("/brands"),
      },
      {
        "@type": "ListItem",
        position: 3,
        name: brand.name,
        item: canonical,
      },
    ],
  };
  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { href: "/brands", label: "الماركات" },
        { label: brand.name },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>ماركة</span>
        <h1>
          أسعار منتجات <bdi dir="auto">{brand.name}</bdi> في مصر
        </h1>
        <p>
          تصفح موديلات <bdi dir="auto">{brand.name}</bdi> وقارن المواصفات
          والأسعار وخيارات الكاش والتقسيط بين المتاجر المصرية.
        </p>
        <div className="seo-listing-stats">
          <b>{brand.products.length.toLocaleString("ar-EG-u-nu-latn")} منتجًا</b>
          <b>{categories.length.toLocaleString("ar-EG-u-nu-latn")} تصنيفًا</b>
        </div>
      </header>
      <section className="seo-directory-section">
        <h2>تصنيفات <bdi dir="auto">{brand.name}</bdi></h2>
        <div className="seo-chip-list">
          {categories.map((entry) => (
            <a href={topCategoryPath(entry)} key={entry.slug}>
              {entry.name}
            </a>
          ))}
        </div>
      </section>
      <section className="seo-directory-section">
        <h2>الموديلات المتاحة</h2>
        <ProductLinks products={brand.products} />
      </section>
    </PublicShell>
  );
}
