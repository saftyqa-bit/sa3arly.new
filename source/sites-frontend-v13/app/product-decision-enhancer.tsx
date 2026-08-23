"use client";

import { useState } from "react";
import type { Product } from "./catalog-selectors";
import DecisionPanel from "./decision-panel";
import StoreQualityOverview from "./store-quality-overview";

export default function ProductDecisionEnhancer({ product }: { product: Product }) {
  const [message, setMessage] = useState("");

  function addToCompare(variantId: string) {
    let current: string[] = [];
    try {
      current = JSON.parse(localStorage.getItem("sa3arly-compare-products") || "[]") as string[];
    } catch {
      current = [];
    }
    if (current.includes(variantId)) {
      setMessage("المنتج موجود بالفعل في المقارنة.");
      return;
    }
    if (current.length >= 4) {
      setMessage("يمكن مقارنة أربعة منتجات كحد أقصى.");
      return;
    }
    localStorage.setItem("sa3arly-compare-products", JSON.stringify([...current, variantId]));
    setMessage("تمت إضافة المنتج للمقارنة. افتح الصفحة الرئيسية لعرض الجدول.");
  }

  function selectVariant(variantId: string) {
    window.location.href = `/?product=${encodeURIComponent(variantId)}#decision-center`;
  }

  return (
    <section className="product-decision-enhancer">
      <div className="product-decision-intro">
        <span>تحليل سعرلي</span>
        <h2>هل هذا وقت مناسب للشراء؟</h2>
        <p>التحليل التالي يعتمد على التكلفة النهائية وتاريخ السعر والمتجر والشحن والتوفر والضمان والمطابقة.</p>
      </div>
      <DecisionPanel product={product} onAddToCompare={addToCompare} onSelectVariant={selectVariant} />
      <StoreQualityOverview variantId={product.id} />
      {message && <button type="button" className="price-toast" onClick={() => setMessage("")}>{message}</button>}
    </section>
  );
}
