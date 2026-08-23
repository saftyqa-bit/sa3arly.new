"use client";

import { useEffect, useMemo, useState } from "react";
import type { DecisionCashOffer, DecisionComparison } from "./decision-types";

const indicators: Array<[keyof DecisionCashOffer, string, boolean]> = [
  ["price_accuracy_score", "دقة الأسعار", false],
  ["update_regularity_score", "انتظام التحديث", false],
  ["availability_clarity_score", "وضوح التوفر", false],
  ["warranty_clarity_score", "وضوح الضمان", false],
  ["correct_destination_score", "الوصول للصفحة الصحيحة", false],
  ["broken_link_rate", "سلامة الروابط", true],
  ["complaint_response_score", "معالجة بلاغات الأسعار", false],
];

function displayScore(value: unknown, inverse: boolean) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const score = inverse ? (1 - value) * 100 : value;
  return Math.round(Math.max(0, Math.min(100, score)));
}

export default function StoreQualityOverview({ variantId }: { variantId: string }) {
  const [offers, setOffers] = useState<DecisionCashOffer[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/live/products/${encodeURIComponent(variantId)}/decision`, { cache: "no-store" })
      .then(async (response) => {
        const body = await response.json() as DecisionComparison & { detail?: string };
        if (!response.ok) throw new Error(body.detail || "quality unavailable");
        return body.cash_offers;
      })
      .then((rows) => {
        if (!cancelled) {
          const unique = new Map<string, DecisionCashOffer>();
          rows.forEach((offer) => {
            const existing = unique.get(offer.store_id);
            if (!existing || (offer.store_quality_sample_size || 0) > (existing.store_quality_sample_size || 0)) unique.set(offer.store_id, offer);
          });
          setOffers([...unique.values()]);
          setState("ready");
        }
      })
      .catch(() => { if (!cancelled) setState("error"); });
    return () => { cancelled = true; };
  }, [variantId]);

  const hasEvidence = useMemo(
    () => offers.some((offer) => indicators.some(([key]) => typeof offer[key] === "number")),
    [offers],
  );

  if (state === "loading") return <section className="store-quality-overview loading">جاري حساب مؤشرات المتاجر…</section>;
  if (state === "error" || !offers.length || !hasEvidence) return null;

  return (
    <section className="store-quality-overview" aria-labelledby="store-quality-title">
      <header><div><span>بيانات منفصلة بدل النجوم</span><h2 id="store-quality-title">مؤشرات جودة المتاجر</h2></div><p>تُحسب من الأسعار والتحديثات والروابط والبلاغات خلال آخر 90 يومًا.</p></header>
      <div className="store-quality-grid">
        {offers.map((offer) => <article key={offer.store_id}>
          <div className="store-quality-name"><b>{offer.store_name}</b><small>عينة: {offer.store_quality_sample_size || 0}</small></div>
          <dl>{indicators.map(([key, label, inverse]) => {
            const score = displayScore(offer[key], inverse);
            return <div key={String(key)}><dt>{label}</dt><dd>{score == null ? <span>بيانات غير كافية</span> : <><meter min="0" max="100" value={score} aria-label={`${label}: ${score} من 100`} /><b>{score}/100</b></>}</dd></div>;
          })}</dl>
        </article>)}
      </div>
      <p className="quality-method-note">هذه ليست مراجعات مستخدمين ولا تقييم نجوم. انخفاض حجم العينة يعني أن المؤشر مبدئي.</p>
    </section>
  );
}
