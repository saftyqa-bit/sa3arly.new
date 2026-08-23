import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { chatGPTSignInPath, getChatGPTUser } from "../chatgpt-auth";
import { getSa3arlyAdmin } from "./admin-auth";
import { loadAdminDashboard } from "./admin-data";
import ReviewDecisionForm from "./review-decision-form";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "إدارة سعرلي",
  robots: { index: false, follow: false },
};

function formatNumber(value: number | null | undefined) {
  if (value == null) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn").format(value);
}

function formatMoney(value: number | null | undefined) {
  if (value == null) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn", {
    style: "currency",
    currency: "EGP",
    maximumFractionDigits: 0,
  }).format(value);
}

function formatDate(value: string | null | undefined) {
  if (!value) return "لا يوجد تحديث";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "وقت غير صالح";
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function severityLabel(value: string) {
  return (
    {
      critical: "حرج",
      high: "مرتفع",
      medium: "متوسط",
      low: "منخفض",
    }[value] ?? value
  );
}

export default async function AdminPage() {
  const signedInUser = await getChatGPTUser();
  const developmentAccess =
    process.env.NODE_ENV !== "production" &&
    Boolean(process.env.SA3ARLY_DEV_ADMIN_EMAIL);
  if (!signedInUser && !developmentAccess) {
    redirect(chatGPTSignInPath("/admin"));
  }

  const admin = await getSa3arlyAdmin();
  if (!admin) notFound();

  const { summary, reviews, products, error } = await loadAdminDashboard();
  const stats = summary?.summary ?? {};
  const cards = [
    ["المنتجات الرئيسية", stats.products],
    ["النسخ", stats.variants],
    ["المتاجر النشطة", stats.active_stores],
    ["الروابط النشطة", stats.active_mappings],
    ["أسعار الكاش", stats.cash_offers],
    ["خطط التقسيط", stats.installment_plans],
    ["مراجعات مفتوحة", stats.open_reviews],
    ["مراجعات عاجلة", stats.urgent_reviews],
  ] as const;

  return (
    <main className="admin-shell" dir="rtl">
      <header className="admin-topbar">
        <div>
          <Link href="/" className="admin-brand">
            <span>س</span>
            <b>سعرلي</b>
          </Link>
          <div>
            <h1>مركز إدارة الكتالوج والأسعار</h1>
            <p>
              قاعدة واحدة للمنتج والنسخ والمتاجر والكاش والتقسيط والمراجعة.
            </p>
          </div>
        </div>
        <aside>
          <small>مسجل الدخول</small>
          <b>{admin.displayName}</b>
          <span>{admin.email}</span>
        </aside>
      </header>

      {summary?.preview && (
        <div className="admin-preview-banner">
          معاينة تصميم محلية — ستُستبدل هذه الأرقام ببيانات PostgreSQL بعد
          تطبيق Migration 005 وربط متغيرات الإدارة.
        </div>
      )}

      {error && (
        <div className="admin-error-banner">
          <b>تعذر تحميل بيانات الإدارة.</b>
          <span>{error}</span>
        </div>
      )}

      <section className="admin-kpi-grid" aria-label="مؤشرات قاعدة البيانات">
        {cards.map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{formatNumber(value)}</strong>
          </article>
        ))}
      </section>

      <section className="admin-status-grid">
        <article className="admin-panel admin-panel-highlight">
          <div className="admin-panel-heading">
            <div>
              <span className="admin-eyebrow">حالة البيانات</span>
              <h2>آخر تحديث فعلي</h2>
            </div>
          </div>
          <dl className="admin-status-list">
            <div>
              <dt>آخر سعر كاش</dt>
              <dd>{formatDate(stats.latest_cash_update)}</dd>
            </div>
            <div>
              <dt>آخر خطة تقسيط</dt>
              <dd>{formatDate(stats.latest_installment_update)}</dd>
            </div>
            <div>
              <dt>مطابقات ضعيفة</dt>
              <dd>{formatNumber(stats.weak_mappings)}</dd>
            </div>
            <div>
              <dt>نسخ مؤقتة</dt>
              <dd>{formatNumber(stats.provisional_variants)}</dd>
            </div>
          </dl>
        </article>

        <article className="admin-panel">
          <div className="admin-panel-heading">
            <div>
              <span className="admin-eyebrow">نموذج البيانات</span>
              <h2>المسار المعتمد</h2>
            </div>
          </div>
          <div className="admin-model-flow" aria-label="مسار قاعدة البيانات">
            <span>تصنيف</span>
            <i>←</i>
            <span>ماركة</span>
            <i>←</i>
            <span>منتج</span>
            <i>←</i>
            <span>نسخة</span>
            <i>←</i>
            <span>متجر</span>
            <i>←</i>
            <span>سعر</span>
          </div>
          <p className="admin-panel-note">
            أي متجر جديد يضيف Mapping فقط. أي لون أو سعة جديدة تضيف Variant
            فقط، بدون إنشاء جداول جديدة أو تكرار المنتج.
          </p>
        </article>
      </section>

      <section className="admin-panel">
        <div className="admin-panel-heading">
          <div>
            <span className="admin-eyebrow">مراجعة بشرية</span>
            <h2>قائمة القرارات المطلوبة</h2>
          </div>
          <span className="admin-count-chip">
            {formatNumber(reviews?.pagination.total)} عنصر
          </span>
        </div>

        <div className="admin-review-list">
          {reviews?.items.length ? (
            reviews.items.map((item) => (
              <article key={item.review_id} className="admin-review-item">
                <div className="admin-review-copy">
                  <div>
                    <span className={`admin-severity ${item.severity}`}>
                      {severityLabel(item.severity)}
                    </span>
                    <code>{item.issue_code}</code>
                  </div>
                  <h3>{item.title}</h3>
                  <p>{item.description ?? "لا توجد ملاحظات إضافية."}</p>
                  <small>
                    {item.entity_type} · {item.entity_id}
                  </small>
                </div>
                <ReviewDecisionForm
                  reviewId={item.review_id}
                  disabled={Boolean(summary?.preview)}
                />
              </article>
            ))
          ) : (
            <p className="admin-empty">لا توجد عناصر مراجعة مفتوحة حاليًا.</p>
          )}
        </div>
      </section>

      <section className="admin-panel">
        <div className="admin-panel-heading">
          <div>
            <span className="admin-eyebrow">كتالوج موحد</span>
            <h2>المنتجات الرئيسية ونسخها</h2>
          </div>
          <span className="admin-count-chip">
            {formatNumber(products?.pagination.total)} منتج
          </span>
        </div>
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>المنتج</th>
                <th>التصنيف</th>
                <th>النسخ</th>
                <th>المتاجر</th>
                <th>أقل كاش</th>
                <th>العروض</th>
              </tr>
            </thead>
            <tbody>
              {products?.items.map((product) => (
                <tr key={product.product_id}>
                  <td>
                    <bdi dir="auto">{product.product_name}</bdi>
                    <small>
                      {product.brand_name ?? "بدون ماركة"} · {product.source_status}
                    </small>
                  </td>
                  <td>
                    {product.category_name ?? "غير مصنف"}
                    <small>{product.parent_category_name ?? "—"}</small>
                  </td>
                  <td>{formatNumber(product.variant_count)}</td>
                  <td>{formatNumber(product.connected_store_count)}</td>
                  <td>{formatMoney(product.lowest_cash_price)}</td>
                  <td>
                    {formatNumber(product.cash_offer_count)} كاش
                    <small>
                      {formatNumber(product.installment_plan_count)} تقسيط
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="admin-bottom-grid">
        <article className="admin-panel">
          <div className="admin-panel-heading">
            <div>
              <span className="admin-eyebrow">المتاجر</span>
              <h2>تحتاج انتباهًا</h2>
            </div>
          </div>
          <div className="admin-store-list">
            {summary?.stores_needing_attention.map((store) => (
              <div key={store.store_id}>
                <span>
                  <b>{store.name}</b>
                  <small>{formatNumber(store.mappings)} ربط</small>
                </span>
                <span>
                  <b>{formatNumber(store.open_reviews)}</b>
                  <small>مراجعة</small>
                </span>
                <span>
                  <b>{formatNumber(store.connector_failures)}</b>
                  <small>فشل متتالٍ</small>
                </span>
              </div>
            ))}
          </div>
        </article>

        <article className="admin-panel">
          <div className="admin-panel-heading">
            <div>
              <span className="admin-eyebrow">التشغيل الآلي</span>
              <h2>آخر دورات الأسعار</h2>
            </div>
          </div>
          <div className="admin-run-list">
            {summary?.recent_runs.map((run) => (
              <div key={run.run_id}>
                <span className={`admin-run-state ${run.status}`}>{run.status}</span>
                <b>{run.trigger_source}</b>
                <small>
                  {formatNumber(run.completed_task_count)}/
                  {formatNumber(run.queued_task_count)} مهمة · فشل {formatNumber(run.failed_task_count)}
                </small>
              </div>
            ))}
          </div>
        </article>
      </section>
    </main>
  );
}
