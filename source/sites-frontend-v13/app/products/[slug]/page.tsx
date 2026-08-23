import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getLiveComparison, installmentTotal } from "../../live-data";
import { buildProductStructuredData, safeJsonLd } from "../../product-schema";
import PublicShell from "../../public-shell";
import {
  absoluteUrl,
  brandEntries,
  brandPath,
  cleanText,
  getProductBySlug,
  getVerifiedProductPresence,
  isIndexableProduct,
  OG_IMAGE_PATH,
  productDisplayName,
  productPath,
  productTopCategory,
  sourceUrl,
  topCategoryPath,
} from "../../seo-data";

export const dynamic = "force-dynamic";

type ProductPageProps = {
  params: Promise<{ slug: string }>;
};

const formatNumber = new Intl.NumberFormat("ar-EG-u-nu-latn");
const formatMoney = new Intl.NumberFormat("ar-EG-u-nu-latn", {
  style: "currency",
  currency: "EGP",
  maximumFractionDigits: 0,
});

function formatDate(value: string | null | undefined) {
  if (!value) return "وقت آخر تحديث غير متاح";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "وقت آخر تحديث غير متاح";
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function availabilityLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    available: "متوفر",
    limited: "كمية محدودة",
    preorder: "طلب مسبق",
    out_of_stock: "غير متوفر حاليًا",
  };
  return value ? labels[value] ?? "التوفر غير مؤكد" : "التوفر غير مؤكد";
}

export async function generateMetadata({
  params,
}: ProductPageProps): Promise<Metadata> {
  const { slug } = await params;
  const product = getProductBySlug(slug);
  if (!product) {
    return {
      title: { absolute: "المنتج غير موجود | سعرلي" },
      robots: { index: false, follow: false },
    };
  }
  const model = cleanText(`${product.brand} ${product.model}`, 120);
  const canonical = absoluteUrl(productPath(product));
  const title = `سعر ${model} في مصر: كاش وتقسيط ومقارنة المتاجر | سعرلي`;
  const description = cleanText(
    `قارن سعر ${model} ومواصفاته بين المتاجر المصرية، واعرض أسعار الكاش وخيارات التقسيط والعروض ووقت آخر تحديث لكل مصدر.`,
    170,
  );
  const indexable = isIndexableProduct(product);
  return {
    title: { absolute: title },
    description,
    alternates: { canonical },
    robots: indexable
      ? {
          index: true,
          follow: true,
          "max-image-preview": "large",
          "max-snippet": -1,
          "max-video-preview": -1,
          googleBot: {
            index: true,
            follow: true,
            "max-image-preview": "large",
            "max-snippet": -1,
            "max-video-preview": -1,
          },
        }
      : { index: false, follow: true },
    openGraph: {
      type: "website",
      siteName: "سعرلي",
      locale: "ar_EG",
      title,
      description,
      url: canonical,
      images: [
        {
          url: absoluteUrl(OG_IMAGE_PATH),
          width: 1200,
          height: 630,
          alt: `مقارنة ${model} على سعرلي`,
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
  };
}

export default async function ProductPage({ params }: ProductPageProps) {
  const { slug } = await params;
  const product = getProductBySlug(slug);
  if (!product) notFound();

  const canonical = absoluteUrl(productPath(product));
  const category = productTopCategory(product);
  const brand = brandEntries.find((entry) =>
    entry.products.some((item) => item.id === product.id),
  );
  const presence = getVerifiedProductPresence(product.id);
  const { cashOffers, installmentPlans, productImageUrl } = await getLiveComparison(product.id);
  const sortedCash = [...cashOffers].sort(
    (left, right) =>
      Number(left.anomaly_status === "review") - Number(right.anomaly_status === "review")
      || left.cash_price - right.cash_price,
  );
  const sortedInstallments = [...installmentPlans].sort(
    (left, right) =>
      (installmentTotal(left) ?? Number.MAX_SAFE_INTEGER) -
      (installmentTotal(right) ?? Number.MAX_SAFE_INTEGER),
  );
  const pricedStores = new Set(
    sortedCash.map((offer) => offer.store_name.toLocaleLowerCase()),
  );
  const installmentStores = new Set(
    sortedInstallments.map((plan) => plan.store_name.toLocaleLowerCase()),
  );
  const withoutCash = presence.filter(
    (item) => !pricedStores.has(item.storeName.toLocaleLowerCase()),
  );
  const withoutInstallment = presence.filter(
    (item) => !installmentStores.has(item.storeName.toLocaleLowerCase()),
  );
  const updateTimes = [
    ...sortedCash.map((offer) => offer.last_success_at),
    ...sortedInstallments.map((plan) => plan.last_success_at),
  ]
    .filter((value): value is string => Boolean(value))
    .sort();
  const latestUpdate = updateTimes.at(-1) ?? null;

  const productSchema = buildProductStructuredData({
    product,
    canonicalUrl: canonical,
    cashOffers: sortedCash.filter((offer) => offer.anomaly_status !== "review"),
  });
  const breadcrumbItems = [
    { name: "سعرلي", url: absoluteUrl("/") },
    { name: "التصنيفات", url: absoluteUrl("/categories") },
    ...(category
      ? [
          {
            name: category.name,
            url: absoluteUrl(topCategoryPath(category)),
          },
        ]
      : []),
    { name: productDisplayName(product), url: canonical },
  ];
  const breadcrumbSchema = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: breadcrumbItems.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: item.url,
    })),
  };

  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { href: "/categories", label: "التصنيفات" },
        ...(category
          ? [{ href: topCategoryPath(category), label: category.name }]
          : []),
        { label: cleanText(product.model, 100) },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(productSchema) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumbSchema) }}
      />

      <article className="seo-product-page">
        <section className="seo-product-hero">
          <figure className="seo-product-visual">
            {productImageUrl ? (
              <img
                src={productImageUrl}
                alt={`صورة منتج ${productDisplayName(product)}`}
                width="720"
                height="720"
                loading="eager"
              />
            ) : (
              <Image
                src={`${productPath(product)}/image`}
                alt={`بطاقة توضيحية لمنتج ${productDisplayName(product)}`}
                width="720"
                height="720"
                unoptimized
              />
            )}
            <figcaption>
              {productImageUrl
                ? "صورة المنتج كما ظهرت في مصدر السعر، وقد تختلف العبوة أو اللون حسب المتجر."
                : "بطاقة تعريفية من بيانات الماركة والموديل لحين توثيق صورة المنتج من المصدر."}
            </figcaption>
          </figure>
          <div className="seo-product-heading">
            <span className="kicker">{cleanText(product.type, 100)}</span>
            <h1>
              سعر <bdi dir="auto">{productDisplayName(product)}</bdi> في مصر
            </h1>
            <p>
              قارن مواصفات المنتج والمتاجر التي ثبت لديها نفس الموديل أو النسخة،
              ثم راجع أسعار الكاش المرصودة، مع تمييز ما يحتاج تحققًا إضافيًا.
            </p>
            <div className="seo-product-facts">
              <span>
                <b>الماركة</b>
                {brand ? (
                  <Link href={brandPath(brand)}>
                    <bdi dir="auto">{brand.name}</bdi>
                  </Link>
                ) : (
                  <bdi dir="auto">{cleanText(product.brand, 100)}</bdi>
                )}
              </span>
              <span>
                <b>الموديل</b>
                <bdi dir="auto">{cleanText(product.model, 120)}</bdi>
              </span>
              <span>
                <b>التصنيف</b>
                {category ? (
                  <Link href={topCategoryPath(category)}>{category.name}</Link>
                ) : (
                  cleanText(product.type, 100)
                )}
              </span>
              <span>
                <b>متاجر بسعر حالي</b>
                {formatNumber.format(pricedStores.size)}
              </span>
            </div>
            <div className="seo-price-summary" aria-label="ملخص الأسعار">
              <div>
                <span>أسعار الكاش المتاحة</span>
                <b>
                  {sortedCash.length
                    ? `${formatNumber.format(sortedCash.length)} سعر`
                    : "لم يُستدل على السعر"}
                </b>
              </div>
              <div>
                <span>خيارات التقسيط المكتملة</span>
                <b>
                  {sortedInstallments.length
                    ? `${formatNumber.format(sortedInstallments.length)} نظام`
                    : "لم يُستدل على نظام مكتمل"}
                </b>
              </div>
              <div>
                <span>وقت آخر تحديث</span>
                <b>{formatDate(latestUpdate)}</b>
              </div>
            </div>
          </div>
        </section>

        <section className="seo-content-card" aria-labelledby="specifications-title">
          <div className="seo-section-heading">
            <span>بيانات النسخة</span>
            <h2 id="specifications-title">أهم المواصفات</h2>
          </div>
          <dl className="seo-spec-list">
            <div>
              <dt>الاسم</dt>
              <dd>
                <bdi dir="auto">{productDisplayName(product)}</bdi>
              </dd>
            </div>
            <div>
              <dt>الفئة الرئيسية</dt>
              <dd>{cleanText(product.section, 100)}</dd>
            </div>
            {product.specs
              .filter((specification) => specification.name !== "اسم النسخة")
              .map((specification) => (
                <div key={`${specification.name}-${specification.value}`}>
                  <dt>{cleanText(specification.name, 100)}</dt>
                  <dd>
                    <bdi dir="auto">
                      {cleanText(specification.value, 160)}
                    </bdi>
                  </dd>
                </div>
              ))}
          </dl>
        </section>

        <section className="seo-content-card" id="cash" aria-labelledby="cash-title">
          <div className="seo-section-heading">
            <span>مقارنة الكاش</span>
            <h2 id="cash-title">أسعار الكاش في المتاجر</h2>
            <p>
              نعرض السعر فقط عندما يكون رقمًا موجبًا ظاهرًا ومطابقًا لنفس
              المنتج. لا يُستخدم الصفر بدل السعر المفقود.
            </p>
          </div>
          {sortedCash.length ? (
            <div className="seo-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>المتجر</th>
                    <th>السعر النقدي</th>
                    <th>حالة التحقق</th>
                    <th>التوفر</th>
                    <th>الشحن</th>
                    <th>آخر تحديث</th>
                    <th>المصدر</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedCash.map((offer) => {
                    const url = sourceUrl(offer.source_url);
                    return (
                      <tr key={offer.offer_id}>
                        <td>
                          <bdi dir="auto">{cleanText(offer.store_name, 100)}</bdi>
                        </td>
                        <td>{formatMoney.format(offer.cash_price)}</td>
                        <td>
                          {offer.anomaly_status === "review"
                            ? "مرصود — قيد التحقق"
                            : "مؤكد آليًا"}
                        </td>
                        <td>{availabilityLabel(offer.availability)}</td>
                        <td>
                          {offer.shipping_cost_known
                            ? offer.shipping_cost === 0
                              ? "شحن مجاني وفق المصدر"
                              : formatMoney.format(offer.shipping_cost ?? 0)
                            : "الشحن غير معلوم"}
                        </td>
                        <td>{formatDate(offer.last_success_at)}</td>
                        <td>
                          {url ? (
                            <a href={url} target="_blank" rel="nofollow noopener">
                              فتح المصدر
                            </a>
                          ) : (
                            "الرابط غير متاح"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="seo-empty-state">
              <b>لم يُستدل على السعر</b>
              <p>
                لا يوجد سعر كاش مؤكد ظاهر لهذه النسخة حاليًا، لذلك لم نضف
                Offer أو سعرًا صفريًا إلى بيانات الصفحة.
              </p>
            </div>
          )}
          <StoreEvidence
            title="متاجر لديها المنتج دون سعر كاش مؤكد"
            items={withoutCash}
            mode="cash"
          />
        </section>

        <section
          className="seo-content-card"
          id="installments"
          aria-labelledby="installments-title"
        >
          <div className="seo-section-heading">
            <span>مقارنة التقسيط</span>
            <h2 id="installments-title">خطط التقسيط والدفعات</h2>
            <p>
              قارن المقدم والقسط الشهري وعدد الشهور وإجمالي المدفوع والرسوم
              والتكلفة الإضافية وشروط الأهلية عندما ينشرها المصدر.
            </p>
          </div>
          {sortedInstallments.length ? (
            <div className="seo-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>المتجر أو الجهة</th>
                    <th>المقدم</th>
                    <th>القسط الشهري</th>
                    <th>عدد الشهور</th>
                    <th>إجمالي المدفوع</th>
                    <th>الرسوم الإدارية</th>
                    <th>الفائدة/التكلفة</th>
                    <th>شروط الأهلية</th>
                    <th>المصدر</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedInstallments.map((plan) => {
                    const url = sourceUrl(plan.source_url);
                    return (
                      <tr key={plan.plan_id}>
                        <td>
                          <bdi dir="auto">{cleanText(plan.store_name, 100)}</bdi>
                          {plan.provider_name && (
                            <small>
                              <bdi dir="auto">
                                {cleanText(plan.provider_name, 100)}
                              </bdi>
                            </small>
                          )}
                        </td>
                        <td>
                          {plan.down_payment == null
                            ? "غير منشور"
                            : formatMoney.format(plan.down_payment)}
                        </td>
                        <td>
                          {plan.periodic_payment == null
                            ? "غير منشور"
                            : formatMoney.format(plan.periodic_payment)}
                        </td>
                        <td>
                          {plan.months == null
                            ? "غير منشور"
                            : formatNumber.format(plan.months)}
                        </td>
                        <td>
                          {installmentTotal(plan) == null
                            ? "غير منشور"
                            : formatMoney.format(installmentTotal(plan)!)}
                        </td>
                        <td>
                          {plan.admin_fees == null
                            ? "غير منشورة"
                            : formatMoney.format(plan.admin_fees)}
                        </td>
                        <td>
                          {plan.interest_free === true
                            ? "بدون فوائد وفق المصدر"
                            : plan.interest_free === false
                              ? "توجد تكلفة إضافية"
                              : "غير محددة"}
                        </td>
                        <td>{cleanText(plan.eligibility, 120) || "غير منشورة"}</td>
                        <td>
                          {url ? (
                            <a href={url} target="_blank" rel="nofollow noopener">
                              فتح المصدر
                            </a>
                          ) : (
                            "الرابط غير متاح"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="seo-empty-state">
              <b>لم يُستدل على نظام تقسيط مكتمل</b>
              <p>
                لا نعرض «بدون فوائد» أو قيمة قسط ما لم تتوفر الشروط والأرقام
                المؤكدة من المصدر.
              </p>
            </div>
          )}
          <StoreEvidence
            title="متاجر لديها المنتج دون نظام تقسيط مكتمل"
            items={withoutInstallment}
            mode="installment"
          />
        </section>
      </article>
    </PublicShell>
  );
}

function StoreEvidence({
  title,
  items,
  mode,
}: {
  title: string;
  items: ReturnType<typeof getVerifiedProductPresence>;
  mode: "cash" | "installment";
}) {
  return (
    <details className="seo-evidence">
      <summary>
        <span>
          <b>{title}</b>
          <small>
            {items.length
              ? `${formatNumber.format(items.length)} مصدرًا — اضغط لعرض التفاصيل`
              : "لا توجد مصادر إضافية مؤكدة"}
          </small>
        </span>
      </summary>
      <div>
        {items.length ? (
          items.map((item) => {
            const url = sourceUrl(item.sourceUrl);
            return (
              <article key={`${mode}-${item.mappingId}`}>
                <span>
                  <b>
                    <bdi dir="auto">{cleanText(item.storeName, 100)}</bdi>
                  </b>
                  <small>{cleanText(item.entityType, 100)}</small>
                </span>
                <span>
                  <b>
                    {mode === "cash"
                      ? cleanText(item.cashLabel, 120) || "السعر غير متاح"
                      : cleanText(item.installmentLabel, 140) ||
                        "لم يُستدل على نظام تقسيط مكتمل"}
                  </b>
                  <small>{cleanText(item.presenceStatus, 140)}</small>
                </span>
                {url ? (
                  <a href={url} target="_blank" rel="nofollow noopener">
                    فتح دليل المنتج
                  </a>
                ) : (
                  <span>الرابط قيد المراجعة</span>
                )}
              </article>
            );
          })
        ) : (
          <p>لم نثبت وجود نفس الموديل أو النسخة لدى مصدر إضافي حتى الآن.</p>
        )}
      </div>
    </details>
  );
}
