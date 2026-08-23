"use client";

import { useEffect, useMemo, useState } from "react";
import type { Product } from "./catalog-selectors";
import type {
  DecisionCashOffer,
  DecisionComparison,
  DecisionInstallmentPlan,
  PriceHistory,
  SmartAlternative,
} from "./decision-types";

const alertRules = [
  ["below_amount", "عند النزول تحت مبلغ معين"],
  ["at_90_day_low", "عند الوصول لأقل سعر خلال 90 يومًا"],
  ["interest_free_installment", "عند ظهور تقسيط بدون فوائد"],
  ["store_available", "عند توفر المنتج في متجر محدد"],
  ["back_in_stock", "عند عودة المخزون"],
  ["coupon_available", "عند ظهور كوبون"],
  ["final_cost_drop", "عند انخفاض التكلفة النهائية بعد الشحن"],
  ["weekly_wishlist_digest", "ملخص أسبوعي لقائمة الرغبات"],
] as const;

const reportTypes = [
  ["wrong_price", "السعر خاطئ"],
  ["wrong_variant", "النسخة أو الموديل مختلف"],
  ["wrong_availability", "حالة التوفر خاطئة"],
  ["broken_link", "الرابط لا يعمل"],
  ["shipping_mismatch", "تكلفة الشحن مختلفة"],
  ["coupon_invalid", "الكوبون لا يعمل"],
  ["warranty_mismatch", "بيانات الضمان مختلفة"],
] as const;

const availabilityLabels: Record<string, string> = {
  available: "متوفر الآن",
  in_stock: "متوفر الآن",
  limited: "كمية محدودة",
  preorder: "طلب مسبق",
  out_of_stock: "غير متوفر",
  unavailable: "غير متوفر",
  unknown: "التوفر غير مؤكد",
};

function money(value: number | null | undefined, currency = "EGP") {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

function fullDate(value: string | null | undefined) {
  if (!value) return "وقت غير متاح";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "وقت غير متاح";
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function relativeTime(value: string | null | undefined) {
  if (!value) return "غير معروف";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "غير معروف";
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000));
  if (minutes < 1) return "الآن";
  if (minutes < 60) return `منذ ${minutes.toLocaleString("ar-EG-u-nu-latn")} دقيقة`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `منذ ${hours.toLocaleString("ar-EG-u-nu-latn")} ساعة`;
  const days = Math.floor(hours / 24);
  return `منذ ${days.toLocaleString("ar-EG-u-nu-latn")} يوم`;
}

function safeUrl(value: string | null | undefined) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function positiveNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 10
    ? value
    : null;
}

function cashOfferSortValue(offer: DecisionCashOffer) {
  return positiveNumber(offer.final_cost)
    ?? positiveNumber(offer.cash_price)
    ?? Number.POSITIVE_INFINITY;
}

function compareCashOffers(left: DecisionCashOffer, right: DecisionCashOffer) {
  const reviewOrder = Number(left.anomaly_status === "review") - Number(right.anomaly_status === "review");
  if (reviewOrder !== 0) return reviewOrder;
  const priceOrder = cashOfferSortValue(left) - cashOfferSortValue(right);
  if (priceOrder !== 0) return priceOrder;
  return left.store_name.localeCompare(right.store_name, "ar", { numeric: true });
}

function emptyHistory(): PriceHistory {
  return {
    lowest_30d: null,
    lowest_90d: null,
    average_90d: null,
    highest_90d: null,
    change_count: 0,
    last_drop_at: null,
    trend: "insufficient_data",
    volatility: 0,
    sparkline: [],
    markers: [],
  };
}

function comparisonFallback(body: unknown, selectedProduct: Product): DecisionComparison {
  const source = body && typeof body === "object" ? body as Record<string, unknown> : {};
  const rawCash = Array.isArray(source.cash_offers) ? source.cash_offers : [];
  const cashOffers = rawCash.flatMap<DecisionCashOffer>((entry, index) => {
    if (!entry || typeof entry !== "object") return [];
    const offer = entry as Record<string, unknown>;
    const cashPrice = positiveNumber(offer.cash_price);
    if (cashPrice == null) return [];
    const shippingCost = typeof offer.shipping_cost === "number" && Number.isFinite(offer.shipping_cost)
      ? offer.shipping_cost
      : null;
    const knownTotal = positiveNumber(offer.comparable_total)
      ?? positiveNumber(offer.total_price);
    const shippingKnown = offer.shipping_cost_known === true
      || offer.free_shipping === true
      || shippingCost != null
      || knownTotal != null;
    const finalCost = knownTotal ?? (shippingKnown ? cashPrice + (shippingCost ?? 0) : cashPrice);
    return [{
      ...offer,
      offer_id: String(offer.offer_id ?? `offer-${index}`),
      store_id: String(offer.store_id ?? ""),
      store_name: String(offer.store_name ?? "متجر"),
      cash_price: cashPrice,
      shipping_cost: shippingCost,
      shipping_cost_known: shippingKnown,
      final_cost: finalCost,
      anomaly_status: offer.anomaly_status === "review" ? "review" : "clear",
      explanation: shippingKnown
        ? "سعر حي من مسار المقارنة الأساسي، ويشمل تكلفة الشحن المعروفة."
        : "سعر حي من مسار المقارنة الأساسي؛ تكلفة الشحن غير معروفة بعد.",
      price_position: {
        label: "سعر حي متاح",
        tone: "unknown",
        percent_vs_average: null,
      },
      match_evidence: { url: typeof offer.source_url === "string" ? offer.source_url : null },
    }];
  }).sort((left, right) => (left.final_cost ?? Infinity) - (right.final_cost ?? Infinity));

  const rawPlans = Array.isArray(source.installment_plans) ? source.installment_plans : [];
  const installmentPlans = rawPlans.flatMap<DecisionInstallmentPlan>((entry, index) => {
    if (!entry || typeof entry !== "object") return [];
    const plan = entry as Record<string, unknown>;
    const finalCost = positiveNumber(plan.normalized_total)
      ?? positiveNumber(plan.total_published)
      ?? positiveNumber(plan.total_calculated);
    if (finalCost == null) return [];
    return [{
      ...plan,
      plan_id: String(plan.plan_id ?? `plan-${index}`),
      store_id: String(plan.store_id ?? ""),
      store_name: String(plan.store_name ?? "متجر"),
      final_installment_cost: finalCost,
      explanation: "خطة حية من مسار المقارنة الأساسي، مرتبة حسب إجمالي المدفوع المتاح.",
    }];
  }).sort((left, right) => (left.final_installment_cost ?? Infinity) - (right.final_installment_cost ?? Infinity));

  const cheapest = cashOffers.map((offer) => offer.offer_id);
  const installment = installmentPlans.map((plan) => plan.plan_id);
  const product = source.product && typeof source.product === "object"
    ? source.product as Record<string, unknown>
    : {
        variant_id: selectedProduct.id,
        canonical_name: selectedProduct.name,
        brand: selectedProduct.brand,
        model: selectedProduct.model,
      };
  return {
    product,
    purchase_index: {
      label: cashOffers.length ? "سعر حي متاح" : "لا يوجد سعر حي لهذه النسخة",
      tone: "unknown",
      percent_vs_average: null,
      explanation: cashOffers.length
        ? "نعرض السعر الأساسي الآن بينما التحليل المتقدم غير متاح مؤقتًا."
        : "سنظل نبحث عن سعر مؤكد لهذه النسخة.",
      best_offer_id: cashOffers[0]?.offer_id ?? null,
    },
    history: emptyHistory(),
    cash_offers: cashOffers,
    installment_plans: installmentPlans,
    mode_orders: { cheapest, safest: cheapest, fastest: cheapest, installment },
    mode_labels: {
      cheapest: "أوفر سعر",
      safest: "أضمن شراء",
      fastest: "أسرع توصيل",
      installment: "أفضل تقسيط",
    },
    alternatives: [],
    known_store_count: new Set(cashOffers.map((offer) => offer.store_id).filter(Boolean)).size,
    degraded_components: ["decision_analysis"],
  };
}

function trendLabel(trend: PriceHistory["trend"]) {
  return {
    stable: "مستقر",
    declining: "يتراجع",
    rising: "يرتفع",
    volatile: "متذبذب",
    insufficient_data: "بيانات غير كافية",
  }[trend];
}

function Sparkline({ history }: { history: PriceHistory }) {
  const points = history.sparkline;
  if (points.length < 2) return <div className="history-placeholder">يحتاج الرسم إلى رصدين ناجحين على الأقل.</div>;
  const values = points.map((item) => item.price);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  const coordinates = points
    .map((item, index) => {
      const x = (index / Math.max(points.length - 1, 1)) * 100;
      const y = 34 - ((item.price - min) / span) * 30;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg className="price-sparkline" viewBox="0 0 100 36" role="img" aria-label="اتجاه السعر خلال آخر 30 يومًا">
      <polyline points={coordinates} fill="none" vectorEffect="non-scaling-stroke" />
      {history.markers.slice(-6).map((marker) => {
        const index = points.findIndex((item) => item.date === marker.date);
        if (index < 0) return null;
        const x = (index / Math.max(points.length - 1, 1)) * 100;
        const y = 34 - ((marker.price - min) / span) * 30;
        return <circle key={`${marker.date}-${marker.label}`} cx={x} cy={y} r="1.5"><title>{marker.label}</title></circle>;
      })}
    </svg>
  );
}

function CostBreakdown({ offer }: { offer: DecisionCashOffer }) {
  const shippingKnown = offer.shipping_cost_known === true || offer.shipping_cost != null;
  return (
    <dl className="cost-breakdown">
      <div><dt>السعر المكتوب</dt><dd dir="ltr">{money(offer.cash_price, offer.currency || "EGP")}</dd></div>
      <div><dt>الشحن</dt><dd>{shippingKnown ? offer.shipping_cost === 0 ? "مجاني" : money(offer.shipping_cost, offer.currency || "EGP") : "غير معروف — غير محسوب"}</dd></div>
      {!!offer.mandatory_fees && <div><dt>رسوم إلزامية</dt><dd dir="ltr">+ {money(offer.mandatory_fees, offer.currency || "EGP")}</dd></div>}
      {!!offer.card_fees && <div><dt>رسوم الكارت</dt><dd dir="ltr">+ {money(offer.card_fees, offer.currency || "EGP")}</dd></div>}
      {!!offer.coupon_discount && <div className="coupon-line"><dt>كوبون {offer.coupon_code || "مطبق"}</dt><dd dir="ltr">− {money(offer.coupon_discount, offer.currency || "EGP")}</dd></div>}
      <div className="total-line"><dt>{shippingKnown ? "التكلفة المعروفة" : "السعر قبل الشحن"}</dt><dd dir="ltr">{money(offer.final_cost ?? offer.cash_price, offer.currency || "EGP")}</dd></div>
    </dl>
  );
}

function InstallmentBreakdown({ plan }: { plan: DecisionInstallmentPlan }) {
  return (
    <dl className="cost-breakdown">
      <div><dt>المقدم</dt><dd dir="ltr">{money(plan.down_payment)}</dd></div>
      <div><dt>الأقساط</dt><dd>{plan.months ? `${plan.months} × ${money(plan.periodic_payment)}` : money(plan.periodic_payment)}</dd></div>
      {!!plan.admin_fees && <div><dt>المصروفات الإدارية</dt><dd dir="ltr">+ {money(plan.admin_fees)}</dd></div>}
      {!!plan.processing_fees && <div><dt>رسوم المعالجة</dt><dd dir="ltr">+ {money(plan.processing_fees)}</dd></div>}
      {!!plan.card_fees && <div><dt>رسوم الكارت</dt><dd dir="ltr">+ {money(plan.card_fees)}</dd></div>}
      {!!plan.shipping_cost && <div><dt>الشحن</dt><dd dir="ltr">+ {money(plan.shipping_cost)}</dd></div>}
      {!!plan.coupon_discount && <div className="coupon-line"><dt>خصم الكوبون</dt><dd dir="ltr">− {money(plan.coupon_discount)}</dd></div>}
      <div className="total-line"><dt>إجمالي المدفوع</dt><dd dir="ltr">{money(plan.final_installment_cost)}</dd></div>
    </dl>
  );
}

function MatchEvidence({ offer }: { offer: DecisionCashOffer }) {
  const evidence = offer.match_evidence;
  return (
    <details className="match-evidence">
      <summary>شاهد دليل المطابقة</summary>
      <dl>
        <div><dt>الحالة</dt><dd>مطابقة مؤكدة: {(evidence?.fields || []).join(" + ") || "الموديل والنسخة"}</dd></div>
        {evidence?.store_sku && <div><dt>SKU المتجر</dt><dd><code>{evidence.store_sku}</code></dd></div>}
        {evidence?.manufacturer_sku && <div><dt>SKU المصنع</dt><dd><code>{evidence.manufacturer_sku}</code></dd></div>}
        {evidence?.title_as_seen && <div><dt>العنوان في المصدر</dt><dd>{evidence.title_as_seen}</dd></div>}
        {evidence?.match_confidence && <div><dt>ثقة المطابقة</dt><dd>{evidence.match_confidence}</dd></div>}
        {evidence?.url && <div><dt>الرابط</dt><dd><a href={safeUrl(evidence.url) || "#"} target="_blank" rel="noreferrer nofollow">فتح الدليل ↗</a></dd></div>}
      </dl>
    </details>
  );
}

function AlertBuilder({ variantId, stores }: { variantId: string; stores: DecisionCashOffer[] }) {
  const [rule, setRule] = useState("below_amount");
  const [threshold, setThreshold] = useState("");
  const [channel, setChannel] = useState("local");
  const [contact, setContact] = useState("");
  const [storeId, setStoreId] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    setMessage("");
    const payload = {
      variant_id: variantId,
      store_id: storeId || null,
      rule_type: rule,
      threshold_amount: threshold ? Number(threshold) : null,
      currency: "EGP",
      channel,
      channel_config: channel === "email" ? { email: contact } : channel === "browser" ? { endpoint: "pending-browser-provider" } : {},
    };
    try {
      if (channel === "local") {
        const saved = JSON.parse(localStorage.getItem("sa3arly-smart-alerts") || "[]") as unknown[];
        localStorage.setItem("sa3arly-smart-alerts", JSON.stringify([...saved, { ...payload, savedAt: new Date().toISOString() }]));
      }
      const response = await fetch("/api/live/alerts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await response.json() as { message?: string; detail?: string };
      if (!response.ok) throw new Error(data.detail || "تعذر حفظ التنبيه");
      setMessage(data.message || "تم حفظ قاعدة التنبيه.");
    } catch (error) {
      if (channel === "local") setMessage("تم حفظ التنبيه محليًا على هذا الجهاز. خدمة المزامنة غير متاحة مؤقتًا.");
      else setMessage(error instanceof Error ? error.message : "تعذر حفظ التنبيه");
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="smart-alert-builder">
      <summary>أنشئ تنبيهًا ذكيًا</summary>
      <div className="alert-form-grid">
        <label><span>متى أنبهك؟</span><select value={rule} onChange={(event) => setRule(event.target.value)}>{alertRules.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        {rule === "below_amount" && <label><span>السعر المستهدف</span><input inputMode="decimal" value={threshold} onChange={(event) => setThreshold(event.target.value.replace(/[^0-9.]/g, ""))} placeholder="مثال: 25000" /></label>}
        {rule === "store_available" && <label><span>المتجر</span><select value={storeId} onChange={(event) => setStoreId(event.target.value)}><option value="">أي متجر</option>{stores.map((offer) => <option key={offer.store_id} value={offer.store_id}>{offer.store_name}</option>)}</select></label>}
        <label><span>القناة</span><select value={channel} onChange={(event) => setChannel(event.target.value)}><option value="local">على هذا الجهاز</option><option value="email">البريد الإلكتروني</option><option value="browser">إشعار المتصفح</option><option value="whatsapp" disabled>واتساب — لاحقًا</option></select></label>
        {channel === "email" && <label><span>البريد</span><input type="email" value={contact} onChange={(event) => setContact(event.target.value)} placeholder="name@example.com" /></label>}
      </div>
      {channel !== "local" && <p className="provider-note">سيتم حفظ القاعدة، لكن الإرسال لن يبدأ قبل ربط مزود القناة والتحقق منها.</p>}
      <button type="button" disabled={saving || (rule === "below_amount" && !threshold)} onClick={() => void submit()}>{saving ? "جاري الحفظ…" : "حفظ التنبيه"}</button>
      {message && <p role="status">{message}</p>}
    </details>
  );
}

function ReportButton({ offer, variantId }: { offer: DecisionCashOffer; variantId: string }) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState("wrong_price");
  const [description, setDescription] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    const response = await fetch("/api/live/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offer_id: offer.offer_id, variant_id: variantId, store_id: offer.store_id, report_type: type, description }),
    });
    setMessage(response.ok ? "شكرًا. أُضيف العرض إلى قائمة المراجعة." : "تعذر إرسال البلاغ مؤقتًا.");
  }

  return (
    <div className="report-price">
      <button type="button" className="text-button" onClick={() => setOpen(!open)}>بلّغ عن سعر خاطئ</button>
      {open && <div className="report-popover"><select value={type} onChange={(event) => setType(event.target.value)}>{reportTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={1000} placeholder="اشرح الفرق باختصار" /><button type="button" onClick={() => void submit()}>إرسال البلاغ</button>{message && <small role="status">{message}</small>}</div>}
    </div>
  );
}

function offerDeliveryLabel(offer: DecisionCashOffer) {
  if (offer.pickup_available) return "استلام من الفرع";
  if (offer.min_delivery_days && offer.max_delivery_days && offer.min_delivery_days !== offer.max_delivery_days) {
    return `${offer.min_delivery_days}–${offer.max_delivery_days} أيام`;
  }
  if (offer.max_delivery_days) return `خلال ${offer.max_delivery_days} أيام`;
  if (offer.min_delivery_days) return `من ${offer.min_delivery_days} أيام`;
  return "موعد غير محدد";
}

function offerWarrantyLabel(offer: DecisionCashOffer) {
  const warranty = `${offer.warranty_type || ""} ${offer.warranty_provider || ""}`;
  if (/official|manufacturer|رسمي/i.test(warranty)) {
    return offer.warranty_months ? `ضمان رسمي ${offer.warranty_months} شهرًا` : "ضمان رسمي";
  }
  if (offer.warranty_months) return `ضمان ${offer.warranty_months} شهرًا`;
  return offer.warranty_type || "الضمان غير موضح";
}

function CashOfferRow({
  offer,
  rank,
  bestPrice,
  recommendedOfferId,
  variantId,
}: {
  offer: DecisionCashOffer;
  rank: number;
  bestPrice: number;
  recommendedOfferId: string | null;
  variantId: string;
}) {
  const source = safeUrl(offer.source_url);
  const total = cashOfferSortValue(offer);
  const difference = Number.isFinite(total) ? Math.max(0, total - bestPrice) : null;
  const freeShipping = offer.shipping_cost === 0;
  const shippingKnown = offer.shipping_cost_known === true || offer.shipping_cost != null;
  const needsReview = offer.anomaly_status === "review";
  const isBest = offer.offer_id === recommendedOfferId && !needsReview;
  const availability = availabilityLabels[offer.availability || "unknown"];

  return (
    <article className={`smart-offer-row ${isBest ? "best" : ""} ${needsReview ? "review-offer" : ""}`}>
      <div className="offer-rank" aria-label={`الترتيب ${rank}`}><b>{rank}</b><small>ترتيب</small></div>
      <div className="offer-merchant">
        <div className="merchant-heading"><b>{offer.store_name}</b>{isBest && <span>أفضل سعر متاح</span>}{needsReview && <span className="review-price-chip">سعر يحتاج مراجعة</span>}</div>
        <small>{offer.seller_name || "البيع من المتجر مباشرة"}</small>
        <div className="offer-signal-row">
          <span className={["available", "in_stock", "limited"].includes(offer.availability || "") ? "positive" : ""}>{availability}</span>
          <span className={freeShipping ? "positive" : ""}>{freeShipping ? "شحن مجاني" : shippingKnown ? `الشحن ${money(offer.shipping_cost, offer.currency || "EGP")}` : "الشحن غير معروف"}</span>
          <span>{offerWarrantyLabel(offer)}</span>
          <span title={fullDate(offer.last_success_at)}>حُدّث {relativeTime(offer.last_success_at)}</span>
        </div>
      </div>
      <div className="offer-price-stack">
        <b dir="ltr">{money(offer.final_cost ?? offer.cash_price, offer.currency || "EGP")}</b>
        <small>{shippingKnown ? "التكلفة المعروفة" : "قبل الشحن"}</small>
        {isBest ? <em>الأقل بين العروض المتاحة</em> : difference != null && difference > 0 ? <em>+ {money(difference, offer.currency || "EGP")} عن الأرخص المتاح</em> : null}
      </div>
      <div className="offer-primary-action">
        {source ? <a href={source} target="_blank" rel="noreferrer nofollow">اختيار العرض ↗</a> : <span>الرابط قيد المراجعة</span>}
      </div>
      <details className="offer-expanded-details">
        <summary>تفاصيل السعر والمتجر</summary>
        <div className="offer-detail-grid">
          <section><h4>كيف وصلنا للسعر؟</h4><CostBreakdown offer={offer} /></section>
          <section>
            <h4>قبل ما تشتري</h4>
            <dl className="offer-facts"><div><dt>التوفر</dt><dd>{availability}</dd></div><div><dt>الضمان</dt><dd>{offerWarrantyLabel(offer)}</dd></div><div><dt>التوصيل</dt><dd>{offerDeliveryLabel(offer)}</dd></div><div><dt>آخر تحقق</dt><dd title={fullDate(offer.last_success_at)}>{relativeTime(offer.last_success_at)}</dd></div></dl>
          </section>
        </div>
        {offer.explanation && <p className="offer-explanation">{offer.explanation}</p>}
        {offer.price_position && <div className={`price-position ${offer.price_position.tone}`}>{offer.price_position.label}</div>}
        <MatchEvidence offer={offer} />
        <div className="offer-detail-footer">{source ? <a href={source} target="_blank" rel="noreferrer nofollow">فتح صفحة المنتج في المتجر ↗</a> : <span>رابط المتجر قيد المراجعة</span>}<ReportButton offer={offer} variantId={variantId} /></div>
      </details>
    </article>
  );
}

function CashOfferTable({
  offers,
  bestPrice,
  recommendedOfferId,
}: {
  offers: DecisionCashOffer[];
  bestPrice: number;
  recommendedOfferId: string | null;
}) {
  return (
    <div className="offer-comparison-table-wrap">
      <table className="offer-comparison-table">
        <thead>
          <tr>
            <th scope="col">المتجر</th>
            <th scope="col">السعر</th>
            <th scope="col">مصاريف الشحن</th>
            <th scope="col">الإجمالي</th>
            <th scope="col">التوفر</th>
            <th scope="col">الضمان</th>
            <th scope="col">آخر تحديث</th>
            <th scope="col">الشراء</th>
          </tr>
        </thead>
        <tbody>
          {offers.map((offer) => {
            const source = safeUrl(offer.source_url);
            const total = cashOfferSortValue(offer);
            const shippingKnown = offer.shipping_cost_known === true || offer.shipping_cost != null;
            const isBest = offer.offer_id === recommendedOfferId && offer.anomaly_status !== "review";
            const difference = Number.isFinite(total) ? Math.max(0, total - bestPrice) : null;
            return (
              <tr key={offer.offer_id} className={`${isBest ? "best" : ""} ${offer.anomaly_status === "review" ? "review" : ""}`.trim()}>
                <td className="table-store-cell"><b>{offer.store_name}</b><small>{offer.seller_name || "البيع من المتجر مباشرة"}</small>{isBest && <span>أقل تكلفة متاحة</span>}{offer.anomaly_status === "review" && <span className="review-price-chip">يحتاج مراجعة</span>}</td>
                <td><b dir="ltr">{money(offer.cash_price, offer.currency || "EGP")}</b><small>سعر المنتج</small></td>
                <td><b>{shippingKnown ? offer.shipping_cost === 0 ? "مجاني" : money(offer.shipping_cost, offer.currency || "EGP") : "غير معروف"}</b><small>{shippingKnown ? offerDeliveryLabel(offer) : "غير محسوب في الإجمالي"}</small></td>
                <td className="table-total-cell"><b dir="ltr">{money(offer.final_cost ?? offer.cash_price, offer.currency || "EGP")}</b><small>{isBest ? "الأقل حاليًا" : difference != null && difference > 0 ? `+ ${money(difference, offer.currency || "EGP")}` : "التكلفة المعروفة"}</small></td>
                <td><b>{availabilityLabels[offer.availability || "unknown"]}</b><small>{offer.pickup_available ? "استلام متاح" : "راجع المتجر قبل الدفع"}</small></td>
                <td><b>{offerWarrantyLabel(offer)}</b><small>{offer.warranty_provider || "حسب بيانات العرض"}</small></td>
                <td><b title={fullDate(offer.last_success_at)}>{relativeTime(offer.last_success_at)}</b><small>{fullDate(offer.last_success_at)}</small></td>
                <td className="table-action-cell">{source ? <a href={source} target="_blank" rel="noreferrer nofollow">اذهب للمتجر ↗</a> : <span>الرابط قيد المراجعة</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AlternativeCard({ alternative, onSelect, onCompare }: { alternative: SmartAlternative; onSelect: (id: string) => void; onCompare: (id: string) => void }) {
  return (
    <article className="alternative-card">
      <span>{alternative.reason}</span>
      <b>{alternative.canonical_name}</b>
      <small>تشابه {Math.round(alternative.similarity_score)}%{alternative.price_gap != null ? ` · فرق ${money(Math.abs(alternative.price_gap))}` : ""}</small>
      <div><button type="button" onClick={() => onSelect(alternative.variant_id)}>اعرض الأسعار</button><button type="button" onClick={() => onCompare(alternative.variant_id)}>أضف للمقارنة</button></div>
    </article>
  );
}

export default function DecisionPanel({
  product,
  onAddToCompare,
  onSelectVariant,
}: {
  product: Product;
  onAddToCompare: (variantId: string) => void;
  onSelectVariant: (variantId: string) => void;
}) {
  const [data, setData] = useState<DecisionComparison | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [tab, setTab] = useState<"offers" | "history" | "installment">("offers");
  const [availableOnly, setAvailableOnly] = useState(false);
  const [officialWarranty, setOfficialWarranty] = useState(false);
  const [pickupOnly, setPickupOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setData(null);
    async function load() {
      try {
        const response = await fetch(
          `/api/live/products/${encodeURIComponent(product.id)}/decision`,
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("decision unavailable");
        const body = await response.json() as DecisionComparison;
        if (!cancelled) { setData(body); setState("ready"); }
        return;
      } catch {
        try {
          const response = await fetch(
            `/api/live/products/${encodeURIComponent(product.id)}/comparison`,
            { cache: "no-store" },
          );
          if (!response.ok) throw new Error("comparison unavailable");
          const fallback = comparisonFallback(await response.json(), product);
          if (!cancelled) { setData(fallback); setState("ready"); }
        } catch {
          if (!cancelled) setState("error");
        }
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [product.id]);

  const rankedCashOffers = useMemo(() => {
    if (!data) return [];
    return [...data.cash_offers].sort(compareCashOffers);
  }, [data]);

  const cashOffers = useMemo(() => {
    if (!data) return [];
    return rankedCashOffers
      .filter((offer) => !availableOnly || ["available", "in_stock", "limited"].includes(offer.availability || ""))
      .filter((offer) => !officialWarranty || /official|manufacturer|رسمي/i.test(`${offer.warranty_type || ""} ${offer.warranty_provider || ""}`))
      .filter((offer) => !pickupOnly || offer.pickup_available);
  }, [availableOnly, data, officialWarranty, pickupOnly, rankedCashOffers]);

  const installmentPlans = useMemo(() => {
    if (!data) return [];
    return [...data.installment_plans].sort((left, right) =>
      (positiveNumber(left.final_installment_cost) ?? Number.POSITIVE_INFINITY)
      - (positiveNumber(right.final_installment_cost) ?? Number.POSITIVE_INFINITY));
  }, [data]);

  if (state === "loading") return <section className="decision-loading">جاري تحميل أسعار المتاجر وترتيبها من الأرخص للأغلى…</section>;
  if (state === "error" || !data) return <section className="decision-unavailable"><h3>تعذر تحميل أسعار المتاجر مؤقتًا.</h3><p>جرّب مرة أخرى بعد قليل؛ لن نعرض سعرًا غير مؤكد على أنه عرض متاح.</p></section>;

  const variantId = String(data.product.variant_id || data.product.id || product.id);
  const purchasableStatuses = ["available", "in_stock", "limited"];
  const recommendedOffer = rankedCashOffers.find((offer) =>
    offer.anomaly_status !== "review" && purchasableStatuses.includes(offer.availability || ""))
    || rankedCashOffers.find((offer) => offer.anomaly_status !== "review")
    || rankedCashOffers[0];
  const bestPrice = recommendedOffer ? cashOfferSortValue(recommendedOffer) : null;
  const trustedPrices = rankedCashOffers
    .filter((offer) => offer.anomaly_status !== "review" && purchasableStatuses.includes(offer.availability || ""))
    .map(cashOfferSortValue)
    .filter(Number.isFinite);
  const highestTrustedPrice = trustedPrices.length ? Math.max(...trustedPrices) : null;
  const priceSpread = bestPrice != null && Number.isFinite(bestPrice) && highestTrustedPrice != null
    ? Math.max(0, highestTrustedPrice - bestPrice)
    : null;
  const confirmedOffers = cashOffers.filter((offer) => offer.anomaly_status !== "review");
  const reviewOffers = cashOffers.filter((offer) => offer.anomaly_status === "review");
  const visibleRecommendedOffer = confirmedOffers.find((offer) => purchasableStatuses.includes(offer.availability || "")) || confirmedOffers[0];
  const visibleBestPrice = visibleRecommendedOffer ? cashOfferSortValue(visibleRecommendedOffer) : bestPrice ?? 0;
  const availableCount = rankedCashOffers.filter((offer) => purchasableStatuses.includes(offer.availability || "")).length;
  const storeCount = data.known_store_count || new Set(rankedCashOffers.map((offer) => offer.store_id || offer.store_name)).size;
  const freshestOffer = [...rankedCashOffers].sort((left, right) => new Date(right.last_success_at || 0).getTime() - new Date(left.last_success_at || 0).getTime())[0];
  const bestSource = safeUrl(recommendedOffer?.source_url);
  const bestShippingKnown = recommendedOffer?.shipping_cost_known === true || recommendedOffer?.shipping_cost != null;

  return (
    <section className="decision-center" id="decision-center">
      <header className={`market-overview ${data.purchase_index.tone}`}>
        <div className="market-product-heading">
          <span>أسعار المنتج في المتاجر</span>
          <h2>{product.name}</h2>
          <p>{rankedCashOffers.length
            ? `${rankedCashOffers.length.toLocaleString("ar-EG-u-nu-latn")} عرض من ${storeCount.toLocaleString("ar-EG-u-nu-latn")} متجر، مرتبة من الأرخص للأغلى بدون الحاجة لإضافة المنتج للمقارنة.`
            : "لم يصلنا عرض كاش صالح لهذه النسخة حتى الآن."}</p>
        </div>
        <div className="market-best-price">
          <small>أفضل سعر متاح الآن</small>
          <b dir="ltr">{bestPrice != null && Number.isFinite(bestPrice) ? money(bestPrice, recommendedOffer?.currency || "EGP") : "—"}</b>
          <span>{bestShippingKnown ? "بالتكلفة المعروفة" : "قبل إضافة الشحن"}</span>
          {bestSource && <a href={bestSource} target="_blank" rel="noreferrer nofollow">اذهب لأفضل عرض ↗</a>}
        </div>
        <div className="market-verdict">
          <span>رأي سعرلي الآن</span>
          <b>{data.purchase_index.label}</b>
          <p>{data.purchase_index.explanation || "القرار محسوب من السعر والتاريخ والشحن والتوفر والضمان وحداثة التحديث."}</p>
          {data.purchase_index.percent_vs_average != null && <em>{data.purchase_index.percent_vs_average < 0 ? "أقل" : "أعلى"} من متوسط 90 يومًا بـ {Math.abs(data.purchase_index.percent_vs_average)}%</em>}
        </div>
        <dl className="market-snapshot">
          <div><dt>فرق الأسعار</dt><dd>{priceSpread != null && priceSpread > 0 ? money(priceSpread, recommendedOffer?.currency || "EGP") : "لا فرق مؤكد"}<small>{priceSpread != null && priceSpread > 0 ? "أقصى توفير محتمل" : "بين العروض المؤكدة"}</small></dd></div>
          <div><dt>المتاح الآن</dt><dd>{availableCount.toLocaleString("ar-EG-u-nu-latn")}<small>من {rankedCashOffers.length.toLocaleString("ar-EG-u-nu-latn")} عرض</small></dd></div>
          <div><dt>اتجاه 90 يومًا</dt><dd>{trendLabel(data.history.trend)}<small>{data.history.lowest_90d ? `الأقل ${money(data.history.lowest_90d)}` : "نحتاج رصدًا أكثر"}</small></dd></div>
          <div><dt>حداثة البيانات</dt><dd>{freshestOffer ? relativeTime(freshestOffer.last_success_at) : "غير متاح"}<small>أحدث تحقق ناجح</small></dd></div>
        </dl>
      </header>

      {!!data.degraded_components?.length && <p className="decision-degraded-notice" role="status">الأسعار الحية ظاهرة من مسار المقارنة الأساسي؛ بعض التحليلات الإضافية غير متاحة مؤقتًا.</p>}

      <div className="comparison-tabs smart-price-tabs" role="tablist" aria-label="معلومات السعر">
        <button type="button" role="tab" aria-selected={tab === "offers"} className={tab === "offers" ? "active" : ""} onClick={() => setTab("offers")}>العروض <span>{data.cash_offers.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>تاريخ السعر</button>
        <button type="button" role="tab" aria-selected={tab === "installment"} className={tab === "installment" ? "active" : ""} onClick={() => setTab("installment")}>التقسيط <span>{data.installment_plans.length}</span></button>
      </div>

      {tab === "offers" && <section className="offers-view" role="tabpanel">
        <div className="offers-control-bar">
          <div><b>قارن السعر الشامل، مش الرقم الكبير بس</b><p>العروض المؤكدة أولًا حسب التكلفة المعروفة، والتفاصيل الكاملة تُفتح عند الحاجة.</p></div>
          <div className="offer-filters"><label><input type="checkbox" checked={availableOnly} onChange={(event) => setAvailableOnly(event.target.checked)} /> متوفر الآن فقط</label><label><input type="checkbox" checked={officialWarranty} onChange={(event) => setOfficialWarranty(event.target.checked)} /> ضمان رسمي</label><label><input type="checkbox" checked={pickupOnly} onChange={(event) => setPickupOnly(event.target.checked)} /> استلام من الفرع</label></div>
        </div>
        {confirmedOffers.length ? <><CashOfferTable offers={confirmedOffers} bestPrice={visibleBestPrice} recommendedOfferId={visibleRecommendedOffer?.offer_id || null} /><div className="decision-offers smart-offer-list mobile-offer-list">{confirmedOffers.map((offer, index) => <CashOfferRow key={offer.offer_id} offer={offer} rank={index + 1} bestPrice={visibleBestPrice} recommendedOfferId={visibleRecommendedOffer?.offer_id || null} variantId={variantId} />)}</div></> : !reviewOffers.length ? <div className="useful-empty-state"><h3>لا توجد عروض تطابق الفلاتر الحالية.</h3><p>ألغِ أحد الفلاتر أو أنشئ تنبيهًا لعودة المخزون.</p></div> : null}
        {!!reviewOffers.length && <section className="review-offers-section"><header><div><span>للمعلومية فقط</span><h3>أسعار تحتاج تحققًا إضافيًا</h3></div><p>نُظهرها لك للشفافية، لكن لا نضعها في صدارة التوصيات حتى تكتمل مراجعتها.</p></header><CashOfferTable offers={reviewOffers} bestPrice={visibleBestPrice} recommendedOfferId={visibleRecommendedOffer?.offer_id || null} /><div className="decision-offers smart-offer-list mobile-offer-list">{reviewOffers.map((offer, index) => <CashOfferRow key={offer.offer_id} offer={offer} rank={confirmedOffers.length + index + 1} bestPrice={visibleBestPrice} recommendedOfferId={visibleRecommendedOffer?.offer_id || null} variantId={variantId} />)}</div></section>}
      </section>}

      {tab === "history" && <section className="history-view" role="tabpanel">
        <header className={`purchase-index ${data.purchase_index.tone}`}>
          <div><span>مؤشر قرار الشراء</span><h2>{data.purchase_index.label}</h2><p>{data.purchase_index.explanation || "يعتمد المؤشر على السعر والتاريخ والمتجر والشحن والتوفر والضمان وحداثة التحديث والمطابقة."}</p></div>
          {data.purchase_index.score != null && <strong>{Math.round(data.purchase_index.score)}<small>/100</small></strong>}
          {data.purchase_index.percent_vs_average != null && <em>{data.purchase_index.percent_vs_average < 0 ? "أقل" : "أعلى"} من متوسط 90 يومًا بـ {Math.abs(data.purchase_index.percent_vs_average)}%</em>}
        </header>
        <section className="history-decision-card">
          <div className="history-heading"><div><span>تاريخ يساعدك على القرار</span><h3>السعر خلال 90 يومًا</h3></div><b className={`trend ${data.history.trend}`}>{trendLabel(data.history.trend)}</b></div>
          <Sparkline history={data.history} />
          <dl className="history-stats"><div><dt>أقل 30 يومًا</dt><dd>{money(data.history.lowest_30d)}</dd></div><div><dt>أقل 90 يومًا</dt><dd>{money(data.history.lowest_90d)}</dd></div><div><dt>المتوسط</dt><dd>{money(data.history.average_90d)}</dd></div><div><dt>الأعلى</dt><dd>{money(data.history.highest_90d)}</dd></div><div><dt>مرات التغير</dt><dd>{data.history.change_count}</dd></div><div><dt>آخر انخفاض</dt><dd title={fullDate(data.history.last_drop_at)}>{data.history.last_drop_at ? relativeTime(data.history.last_drop_at) : "غير متاح"}</dd></div></dl>
        </section>
        <AlertBuilder variantId={variantId} stores={data.cash_offers} />
      </section>}

      {tab === "installment" && <section role="tabpanel"><div className="installment-intro"><b>رتّبنا التقسيط حسب إجمالي ما ستدفعه</b><p>القسط الشهري وحده قد يكون مضللًا؛ لذلك نظهر المقدم والرسوم والإجمالي.</p></div><div className="decision-offers installment-offers">{installmentPlans.length ? installmentPlans.map((plan, index) => <article key={plan.plan_id} className={index === 0 ? "best" : ""}><header><div><b>{plan.store_name}</b><small>{plan.provider_name || plan.bank_or_card || "خطة المتجر"}</small></div><div className="offer-chips">{index === 0 && <span>أقل إجمالي مدفوع</span>}{plan.interest_free && <span>بدون فوائد</span>}</div></header><div className="decision-offer-price"><b dir="ltr">{money(plan.final_installment_cost)}</b><small>إجمالي المدفوع</small></div><div className="monthly-payment"><b>{money(plan.periodic_payment)}</b> شهريًا {plan.months ? `لمدة ${plan.months} شهرًا` : ""}</div>{plan.low_payment_high_total && <p className="cost-warning">القسط منخفض لكن الإجمالي مرتفع مقارنة بسعر الكاش.</p>}<p>{plan.explanation}</p><InstallmentBreakdown plan={plan} />{safeUrl(plan.source_url) ? <a href={safeUrl(plan.source_url)!} target="_blank" rel="noreferrer nofollow">اذهب للخطة ↗</a> : <span>رابط الخطة قيد المراجعة</span>}</article>) : <div className="useful-empty-state"><h3>لم نجد خطة تقسيط مكتملة لهذه النسخة.</h3><p>لن نصنف خطة على أنها الأفضل اعتمادًا على القسط فقط.</p></div>}</div></section>}

      <details className="secondary-decision-tools">
        <summary>أدوات إضافية: المقارنة ونسخ الموديل</summary>
        <div className="decision-toolbar"><div className="secondary-tools"><b>اختيارية بعد مشاهدة الأسعار</b><p>يمكنك إضافة المنتج للمقارنة مع منتج آخر، لكن ذلك ليس مطلوبًا لعرض أسعاره.</p></div><div className="decision-actions"><button type="button" onClick={() => onAddToCompare(variantId)}>أضف للمقارنة</button><button type="button" onClick={() => navigator.clipboard.writeText(String(product.model || product.name))}>نسخ رقم الموديل</button></div></div>
      </details>

      {data.alternatives.length > 0 && <section className="smart-alternatives"><div><span>بدائل بالمواصفات الفعلية</span><h3>قد يناسبك أيضًا</h3></div><div className="alternative-grid">{data.alternatives.slice(0, 8).map((alternative) => <AlternativeCard key={alternative.variant_id} alternative={alternative} onSelect={onSelectVariant} onCompare={onAddToCompare} />)}</div></section>}
    </section>
  );
}
