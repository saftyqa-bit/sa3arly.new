import Link from "next/link";
import ListingCompareButton from "./listing-compare-button";
import PublicShell from "./public-shell";
import { safeJsonLd } from "./product-schema";
import { absoluteUrl, cleanText, slugify, topCategoryEntries } from "./seo-data";
import { getPricedProducts, type PricedProductSummary } from "./live-data";
import { productNameParts } from "./display-names";

const PAGE_SIZE = 24;

export function parsePageParam(
  searchParams: Record<string, string | string[] | undefined>,
): number {
  const raw = Array.isArray(searchParams.page) ? searchParams.page[0] : searchParams.page;
  const page = raw ? Number.parseInt(raw, 10) : 1;
  return Number.isFinite(page) && page > 1 ? page : 1;
}

export function parseListingFilters(
  searchParams: Record<string, string | string[] | undefined>,
) {
  const first = (value: string | string[] | undefined) => Array.isArray(value) ? value[0] : value;
  return {
    query: String(first(searchParams.q) ?? "").trim().slice(0, 160),
    section: String(first(searchParams.section) ?? "").trim().slice(0, 120),
  };
}

function formatMoney(value: number | null, currency = "EGP") {
  if (value == null || !Number.isFinite(value)) return null;
  return new Intl.NumberFormat("ar-EG-u-nu-latn", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

function storeCountLabel(count: number) {
  if (count <= 0) return "";
  if (count === 1) return "متاح لدى متجر واحد";
  return `متاح لدى ${count.toLocaleString("ar-EG-u-nu-latn")} متاجر`;
}

function productHref(item: PricedProductSummary) {
  return `/products/${slugify(item.brand)}-${slugify(item.model)}-${item.variantId.toLocaleLowerCase("en-US")}`;
}

function ListingSparkline({ item }: { item: PricedProductSummary }) {
  const points = item.priceHistory.sparkline;
  if (points.length < 2) return <span className="listing-sparkline-empty">تاريخ السعر قيد التجميع</span>;
  const values = points.map((point) => point.price);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, 1);
  const coordinates = points.map((point, index) => {
    const x = (index / Math.max(points.length - 1, 1)) * 100;
    const y = 26 - ((point.price - minimum) / span) * 22;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg className="listing-sparkline" viewBox="0 0 100 28" role="img" aria-label="تغير السعر خلال آخر 30 يومًا">
      <polyline points={coordinates} fill="none" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function PricedProductGrid({
  items,
  isCash,
}: {
  items: PricedProductSummary[];
  isCash: boolean;
}) {
  return (
    <div className="seo-product-grid phase-two-listing-grid">
      {items.map((item) => {
        const reviewRequired = isCash
          ? item.cashPriceReviewRequired
          : item.installmentPriceReviewRequired;
        const deliveredCostKnown = isCash
          && !reviewRequired
          && item.lowestDeliveredTotal != null;
        const price = isCash
          ? formatMoney(
            item.lowestDeliveredTotal
              ?? item.lowestFinalCost
              ?? item.lowestConfirmedCashPrice
              ?? item.lowestCashPrice,
          )
          : formatMoney(
            item.lowestInstallmentTotal
              ?? item.lowestVisibleInstallmentTotal
              ?? item.lowestPeriodicPayment
              ?? item.lowestVisiblePeriodicPayment,
          );
        const priceLabel = reviewRequired
          ? "سعر مرصود — يحتاج مراجعة"
          : isCash
            ? deliveredCostKnown
              ? "التكلفة بعد الشحن من"
              : "سعر الكاش من — الشحن غير محسوب"
            : item.lowestInstallmentTotal != null
              ? "أقل إجمالي تقسيط مؤكد"
              : "قسط دوري مؤكد";
        const storeCount = isCash ? item.cashOfferCount : item.installmentPlanCount;
        return (
          <article key={item.variantId} className="phase-two-product-card">
            <Link href={productHref(item)} className="phase-two-product-link">
              {item.imageUrl && <img className="listing-product-image" src={item.imageUrl} alt="" loading="lazy" />}
              <span>{cleanText(item.productType ?? item.section, 80)}</span>
              <b><bdi dir="auto">{cleanText(productNameParts(item.brand, item.model, item.variantName), 90)}</bdi></b>
              <div className="listing-price-line">
                {price ? (
                  <><strong dir="ltr">{price}</strong><small>{priceLabel}</small></>
                ) : (
                  <small>السعر قيد الرصد</small>
                )}
              </div>
              {reviewRequired && <em className="listing-review-label">ظاهر للشفافية — لا يدخل في التوصيات</em>}
              {!reviewRequired && item.purchaseLabel && <em className="listing-purchase-label">{item.purchaseLabel}</em>}
              <ListingSparkline item={item} />
              <small>{storeCount ? storeCountLabel(storeCount) : ""}</small>
              <em className="listing-open-link">افتح تحليل الشراء ←</em>
            </Link>
            <ListingCompareButton variantId={item.variantId} />
          </article>
        );
      })}
    </div>
  );
}

function Pagination({
  basePath,
  page,
  totalPages,
  total,
  query,
  section,
}: {
  basePath: string;
  page: number;
  totalPages: number;
  total: number;
  query?: string;
  section?: string;
}) {
  const hrefFor = (targetPage: number) => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (section) params.set("section", section);
    if (targetPage > 1) params.set("page", String(targetPage));
    return `${basePath}${params.size ? `?${params}` : ""}`;
  };
  return (
    <nav className="seo-pagination" aria-label="ترقيم صفحات المنتجات">
      {page > 1 && (
        <Link href={hrefFor(page - 1)} rel="prev">
          الصفحة السابقة
        </Link>
      )}
      <span>
        صفحة {page.toLocaleString("ar-EG-u-nu-latn")} من {totalPages.toLocaleString("ar-EG-u-nu-latn")} (
        {total.toLocaleString("ar-EG-u-nu-latn")} منتجًا له سعر مؤكد الآن)
      </span>
      {page < totalPages && (
        <Link href={hrefFor(page + 1)} rel="next">الصفحة التالية</Link>
      )}
    </nav>
  );
}

export default async function ComparisonLanding({
  mode,
  page = 1,
  query = "",
  section = "",
}: {
  mode: "cash" | "installments";
  page?: number;
  query?: string;
  section?: string;
}) {
  const isCash = mode === "cash";
  const safePage = Number.isFinite(page) && page > 1 ? Math.floor(page) : 1;
  const basePath = isCash ? "/cash" : "/installments";
  const { items, total } = await getPricedProducts(isCash ? "cash" : "installment", {
    limit: PAGE_SIZE,
    offset: (safePage - 1) * PAGE_SIZE,
    query,
    section,
  });
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const title = isCash
    ? "مقارنة التكلفة النهائية للكاش في متاجر مصر"
    : "مقارنة إجمالي تقسيط المنتجات في مصر";
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "سعرلي", item: absoluteUrl("/") },
      {
        "@type": "ListItem",
        position: 2,
        name: isCash ? "مقارنة الكاش" : "مقارنة التقسيط",
        item: absoluteUrl(basePath),
      },
    ],
  };

  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { label: isCash ? "مقارنة الكاش" : "مقارنة التقسيط" },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>{isCash ? "تكلفة الشراء الحقيقية" : "إجمالي المدفوع الحقيقي"}</span>
        <h1>{title}</h1>
        <p>
          {isCash
            ? "مرتبة حسب السعر والشحن والرسوم والكوبون، مع مؤشر مبسط وتاريخ سعر مصغر."
            : "مرتبة حسب المقدم ومجموع الأقساط والرسوم والشحن، لا حسب القسط الشهري وحده."}
        </p>
        <Link className="seo-primary-link" href="/#selector">اختر منتجًا وابدأ المقارنة</Link>
      </header>

      {isCash ? (
        <section className="seo-content-card">
          <div className="seo-section-heading"><span>كيف نحسب الكاش؟</span><h2>السعر + الشحن + الرسوم − الكوبون</h2></div>
          <div className="seo-definition-grid">
            <article><b>السعر النقدي</b><p>السعر المنشور لنفس الموديل والنسخة دون استخدام صفر.</p></article>
            <article><b>التكلفة النهائية</b><p>يدخل فيها الشحن والرسوم الإلزامية والكوبون عندما تكون البيانات مؤكدة.</p></article>
            <article><b>تاريخ السعر</b><p>بطاقة المنتج تعرض اتجاهًا مصغرًا لآخر 30 يومًا.</p></article>
            <article><b>وقت آخر تحديث</b><p>لا يظهر إلا بعد رصد ناجح فعليًا.</p></article>
          </div>
        </section>
      ) : (
        <section className="seo-content-card">
          <div className="seo-section-heading"><span>عناصر المقارنة</span><h2>تفاصيل التكلفة الكاملة للتقسيط</h2></div>
          <div className="seo-definition-grid">
            {[
              ["المقدم", "المبلغ المطلوب قبل بدء الأقساط."],
              ["مجموع الأقساط", "القسط الدوري مضروبًا في عدد الدفعات."],
              ["المصروفات والرسوم", "الإدارية والمعالجة والكارت والتأمين عندما تكون منشورة."],
              ["الشحن", "يضاف إلى الإجمالي عندما يكون معلومًا."],
              ["الكوبون", "يخصم من الإجمالي فقط بعد ظهوره كبيان صالح."],
              ["أقل إجمالي تقسيط", "الشارة تذهب لأقل مدفوع حقيقي، لا لأقل قسط."],
            ].map(([heading, copy]) => (
              <article key={heading}><b>{heading}</b><p>{copy}</p></article>
            ))}
          </div>
        </section>
      )}

      <section className="seo-directory-section">
        <h2>{isCash ? "منتجات لها تكلفة كاش مؤكدة الآن" : "منتجات لها إجمالي تقسيط مؤكد الآن"}</h2>
        <form className="listing-filter-form" action={basePath} method="get">
          <label>
            <span>ابحث في المنتجات المرصودة</span>
            <input name="q" defaultValue={query} placeholder="اسم المنتج أو الماركة أو الموديل" />
          </label>
          <label>
            <span>الفئة</span>
            <select name="section" defaultValue={section}>
              <option value="">كل الفئات</option>
              {topCategoryEntries.map((entry) => (
                <option value={entry.sectionName} key={entry.slug}>{entry.name}</option>
              ))}
            </select>
          </label>
          <button type="submit">تطبيق</button>
          {(query || section) && <Link href={basePath}>مسح الفلاتر</Link>}
        </form>
        {items.length ? (
          <>
            <PricedProductGrid items={items} isCash={isCash} />
            <Pagination basePath={basePath} page={safePage} totalPages={totalPages} total={total} query={query} section={section} />
          </>
        ) : (
          <p className="seo-list-note">
            لا توجد منتجات لها {isCash ? "سعر كاش" : "خطة تقسيط"} مؤكدة حاليًا؛ جاري توسيع تغطية الرصد لمتاجر إضافية.
          </p>
        )}
      </section>
    </PublicShell>
  );
}
