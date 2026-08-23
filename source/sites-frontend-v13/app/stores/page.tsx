import type { Metadata } from "next";
import Link from "next/link";
import PublicShell from "../public-shell";
import { safeJsonLd } from "../product-schema";
import { absoluteUrl, storeEntries, storePath } from "../seo-data";
import { getLiveStores } from "../live-data";

export const metadata: Metadata = {
  title: { absolute: "متاجر مصر وأسعار المنتجات المتاحة | سعرلي" },
  description:
    "تصفح المتاجر والمصادر التي لديها منتجات مرتبطة فعليًا في سعرلي، ثم قارن كل منتج مع المتاجر الأخرى وراجع وقت التحديث.",
  alternates: { canonical: absoluteUrl("/stores") },
  robots: { index: true, follow: true },
};

function normalizeStoreName(value: string) {
  return value.normalize("NFKC").toLocaleLowerCase("ar-EG").replace(/\begypt\b|مصر/g, "").replace(/[^\p{L}\p{N}]+/gu, "").trim();
}

export default async function StoresPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const rawQuery = Array.isArray(params.q) ? params.q[0] : params.q;
  const query = String(rawQuery ?? "").trim().slice(0, 120);
  const liveStores = await getLiveStores(query);
  const staticByName = new Map(
    storeEntries.map((store) => [normalizeStoreName(store.name), store]),
  );
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
    ],
  };
  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { label: "المتاجر" },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>دليل المتاجر</span>
        <h1>دليل متاجر مصر وحالة رصد الأسعار</h1>
        <p>
          يعرض الدليل كل متجر مسجل بوضوح، ويميز بين متجر له ربط بالمنتجات
          ومتجر له أسعار حية الآن. الإدراج لا يعني وجود شراكة رسمية مع سعرلي.
        </p>
        <form className="store-directory-search" action="/stores" method="get">
          <input name="q" defaultValue={query} placeholder="ابحث عن 2B أو Vodafone أو IKEA..." aria-label="ابحث في دليل المتاجر" />
          <button type="submit">بحث</button>
        </form>
      </header>
      <section className="seo-directory-section">
        <h2>
          {liveStores.length
            ? `${liveStores.length.toLocaleString("ar-EG-u-nu-latn")} متجرًا مطابقًا`
            : "المتاجر المرتبطة بمنتجات موثقة"}
        </h2>
        <div className="seo-directory-grid">
          {liveStores.length
            ? liveStores.map((store) => {
                const staticStore = staticByName.get(normalizeStoreName(store.name));
                const content = (
                  <>
                    <b><bdi dir="auto">{store.name}</bdi></b>
                    <span>
                      {store.verifiedPricedProductCount > 0
                        ? "أسعار مؤكدة متاحة"
                        : store.reviewPricedProductCount > 0
                          ? "أسعار مرصودة — قيد التحقق"
                          : store.connected
                          ? "مرتبط بمنتجات — الرصد قيد التوسيع"
                          : "مسجل — لم يبدأ الرصد بعد"}
                    </span>
                    <small>
                      {store.verifiedPricedProductCount > 0
                        ? `${store.verifiedPricedProductCount.toLocaleString("ar-EG-u-nu-latn")} منتجًا بسعر مؤكد`
                        : store.reviewPricedProductCount > 0
                          ? `${store.reviewPricedProductCount.toLocaleString("ar-EG-u-nu-latn")} منتجًا بسعر قيد التحقق`
                        : store.mappedProductCount > 0
                          ? `${store.mappedProductCount.toLocaleString("ar-EG-u-nu-latn")} منتجًا مرتبطًا`
                          : store.primaryCategory || store.storeType || "مصدر بيع"}
                    </small>
                  </>
                );
                return staticStore ? (
                  <Link href={storePath(staticStore)} key={store.storeId} className="store-directory-card">
                    {content}
                  </Link>
                ) : (
                  <article key={store.storeId} className="store-directory-card">
                    {content}
                  </article>
                );
              })
            : storeEntries.map((store) => (
                <Link href={storePath(store)} key={store.id} className="store-directory-card">
                  <b><bdi dir="auto">{store.name}</bdi></b>
                  <span>{store.entityType || "مصدر بيع أو توافر"}</span>
                  <small>{store.products.length.toLocaleString("ar-EG-u-nu-latn")} منتج</small>
                </Link>
              ))}
        </div>
        {!liveStores.length && query && (
          <p className="seo-list-note">لا يوجد متجر مطابق لعبارة البحث الحالية.</p>
        )}
      </section>
    </PublicShell>
  );
}
