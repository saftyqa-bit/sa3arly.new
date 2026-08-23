import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ProductLinks from "../../product-links";
import PublicShell from "../../public-shell";
import { safeJsonLd } from "../../product-schema";
import {
  absoluteUrl,
  cleanText,
  getVerifiedProductPresence,
  getStoreBySlug,
  OG_IMAGE_PATH,
  sourceUrl,
  storePath,
} from "../../seo-data";

type StorePageProps = { params: Promise<{ slug: string }> };

export async function generateMetadata({
  params,
}: StorePageProps): Promise<Metadata> {
  const { slug } = await params;
  const store = getStoreBySlug(slug);
  if (!store || !store.products.length) {
    return {
      title: { absolute: "المتجر غير متاح | سعرلي" },
      robots: { index: false, follow: false },
    };
  }
  const name = cleanText(store.name, 100);
  const title = `أسعار وعروض ${name} ومقارنة المنتجات | سعرلي`;
  const description = `تصفح المنتجات والأسعار والعروض المتاحة لدى ${name} مع وقت آخر تحديث وروابط المقارنة مع متاجر أخرى.`;
  const canonical = absoluteUrl(storePath(store));
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

export default async function StorePage({ params }: StorePageProps) {
  const { slug } = await params;
  const store = getStoreBySlug(slug);
  if (!store || !store.products.length) notFound();
  const source = store.products
    .flatMap((product) => getVerifiedProductPresence(product.id))
    .find((item) => item.storeId === store.id && sourceUrl(item.sourceUrl));
  const storeUrl = sourceUrl(source?.sourceUrl);
  const canonical = absoluteUrl(storePath(store));
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "سعرلي", item: absoluteUrl("/") },
      {
        "@type": "ListItem",
        position: 2,
        name: "المتاجر",
        item: absoluteUrl("/stores"),
      },
      {
        "@type": "ListItem",
        position: 3,
        name: store.name,
        item: canonical,
      },
    ],
  };
  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { href: "/stores", label: "المتاجر" },
        { label: store.name },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>{store.entityType || "متجر أو مصدر بيع"}</span>
        <h1>
          أسعار وعروض <bdi dir="auto">{store.name}</bdi>
        </h1>
        <p>
          تصفح المنتجات المرتبطة فعليًا بهذا المصدر وافتح صفحة كل منتج لمقارنة
          الأسعار والعروض ووقت آخر تحديث مع متاجر أخرى.
        </p>
        <div className="seo-listing-stats">
          <b>{store.products.length.toLocaleString("ar-EG-u-nu-latn")} منتجًا</b>
          {storeUrl && (
            <a href={storeUrl} target="_blank" rel="nofollow noopener">
              فتح مصدر المتجر
            </a>
          )}
        </div>
        <small>
          سعرلي موقع مقارنة مستقل؛ لا ندّعي وجود شراكة رسمية مع المتجر.
        </small>
      </header>
      <section className="seo-directory-section">
        <h2>المنتجات المتاحة للمقارنة</h2>
        <ProductLinks products={store.products} limit={200} />
      </section>
    </PublicShell>
  );
}
