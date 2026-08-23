"use client";

import { useEffect, useMemo, useState } from "react";
import type { Product } from "./catalog-selectors";
import type { ProductComparisonMatrix } from "./decision-types";
import { collapseRepeatedName, modelNameParts } from "./display-names";

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn", {
    style: "currency",
    currency: "EGP",
    maximumFractionDigits: 0,
  }).format(value);
}

function productName(product: Product) {
  return modelNameParts(product.brand, product.model || product.name);
}

function drawComparisonImage(matrix: ProductComparisonMatrix, differencesOnly: boolean) {
  const rows = matrix.matrix.filter((row) => {
    if (!differencesOnly) return true;
    const values = Object.values(row.values).filter(Boolean);
    return new Set(values).size > 1;
  }).slice(0, 14);
  const width = 1200;
  const headerHeight = 160;
  const productHeight = 130;
  const rowHeight = 64;
  const height = headerHeight + productHeight + rows.length * rowHeight + 100;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#f7f8fb";
  ctx.fillRect(0, 0, width, height);
  ctx.direction = "rtl";
  ctx.textAlign = "right";
  ctx.fillStyle = "#111827";
  ctx.font = "700 52px Arial";
  ctx.fillText("مقارنة سعرلي", width - 60, 75);
  ctx.font = "24px Arial";
  ctx.fillStyle = "#4b5563";
  ctx.fillText("السعر والمواصفات والقيمة مقابل السعر", width - 60, 118);
  const columnWidth = (width - 260) / matrix.products.length;
  matrix.products.forEach((product, index) => {
    const x = width - 40 - index * columnWidth;
    ctx.fillStyle = product.variant_id === matrix.best_value_variant_id ? "#e8f7ef" : "#ffffff";
    ctx.fillRect(x - columnWidth + 10, headerHeight, columnWidth - 18, productHeight - 12);
    ctx.fillStyle = "#111827";
    ctx.font = "700 22px Arial";
    ctx.fillText(collapseRepeatedName(product.canonical_name).slice(0, 30), x - 15, headerHeight + 35, columnWidth - 35);
    ctx.font = "20px Arial";
    ctx.fillText(money(product.lowest_final_cost), x - 15, headerHeight + 72);
    ctx.fillStyle = "#047857";
    ctx.fillText(matrix.best_value_variant_id ? `قيمة ${Math.round(product.value_score || 0)}/100` : "بيانات السعر غير كافية", x - 15, headerHeight + 105);
  });
  rows.forEach((row, rowIndex) => {
    const y = headerHeight + productHeight + rowIndex * rowHeight;
    ctx.fillStyle = rowIndex % 2 ? "#ffffff" : "#f1f5f9";
    ctx.fillRect(40, y, width - 80, rowHeight);
    ctx.fillStyle = "#374151";
    ctx.font = "700 19px Arial";
    ctx.fillText(row.name.slice(0, 22), width - 45, y + 38, 205);
    matrix.products.forEach((product, index) => {
      const x = width - 260 - index * columnWidth;
      ctx.font = "18px Arial";
      ctx.fillStyle = "#111827";
      ctx.fillText(String(row.values[product.variant_id] || "—").slice(0, 22), x, y + 38, columnWidth - 30);
    });
  });
  ctx.fillStyle = "#6b7280";
  ctx.font = "18px Arial";
  ctx.fillText("تم إنشاء الصورة من البيانات المتاحة وقت المقارنة — راجع المتجر قبل الشراء", width - 50, height - 35);
  return canvas;
}

export default function ProductCompareDrawer({
  selectedIds,
  catalogProducts,
  onRemove,
  onClear,
}: {
  selectedIds: string[];
  catalogProducts: Product[];
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [matrix, setMatrix] = useState<ProductComparisonMatrix | null>(null);
  const [loading, setLoading] = useState(false);
  const [differencesOnly, setDifferencesOnly] = useState(false);
  const [message, setMessage] = useState("");

  const selected = useMemo(
    () => selectedIds.map((id) => catalogProducts.find((product) => product.id === id)).filter((product): product is Product => Boolean(product)),
    [catalogProducts, selectedIds],
  );

  useEffect(() => {
    localStorage.setItem("sa3arly-compare-products", JSON.stringify(selectedIds));
    if (selectedIds.length < 2) {
      setMatrix(null);
      return;
    }
    setOpen(true);
    let cancelled = false;
    setLoading(true);
    fetch("/api/live/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant_ids: selectedIds }),
    })
      .then(async (response) => {
        const body = await response.json() as ProductComparisonMatrix & { detail?: string };
        if (!response.ok) throw new Error(body.detail || "تعذر إنشاء المقارنة");
        return body;
      })
      .then((body) => { if (!cancelled) setMatrix(body); })
      .catch((error) => { if (!cancelled) setMessage(error instanceof Error ? error.message : "تعذر إنشاء المقارنة"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedIds]);

  async function shareImage() {
    if (!matrix) return;
    const canvas = drawComparisonImage(matrix, differencesOnly);
    if (!canvas) return;
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) return;
    const file = new File([blob], "sa3arly-comparison.png", { type: "image/png" });
    try {
      if (navigator.share && navigator.canShare?.({ files: [file] })) {
        await navigator.share({ title: "مقارنة سعرلي", text: matrix.explanation || "مقارنة منتجات من سعرلي", files: [file] });
        return;
      }
    } catch {
      // The user can still download the image below.
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.name;
    anchor.click();
    URL.revokeObjectURL(url);
    setMessage("تم تجهيز صورة المقارنة؛ يمكنك إرسالها على واتساب.");
  }

  if (!selectedIds.length) return null;

  const rows = (matrix?.matrix || []).filter((row) => {
    if (!differencesOnly) return true;
    const values = Object.values(row.values).filter(Boolean);
    return new Set(values).size > 1;
  });

  return (
    <aside className={`compare-drawer ${open ? "open" : ""}`} aria-label="مقارنة المنتجات">
      <button type="button" className="compare-drawer-toggle" onClick={() => setOpen(!open)}>
        مقارنة المنتجات <span>{selectedIds.length}/4</span>
      </button>
      {open && <div className="compare-drawer-panel">
        <header><div><span>مقارنة حتى أربعة منتجات</span><h2>مقارنة المنتجات</h2></div><button type="button" onClick={onClear}>مسح الكل</button></header>
        <div className="compare-selected-list">
          {selected.map((product) => <article key={product.id}><b>{productName(product)}</b><small>{product.variant || product.type}</small><button type="button" aria-label={`إزالة ${productName(product)}`} onClick={() => onRemove(product.id)}>×</button></article>)}
          {Array.from({ length: Math.max(0, 4 - selected.length) }).map((_, index) => <div className="compare-empty-slot" key={index}>أضف منتجًا</div>)}
        </div>
        {selected.length < 2 && <p className="compare-help">أضف منتجين على الأقل لعرض جدول المقارنة.</p>}
        {loading && <p>جاري حساب القيمة والفروق…</p>}
        {matrix && <>
          <div className="compare-summary"><p>{matrix.explanation}</p><label><input type="checkbox" checked={differencesOnly} onChange={(event) => setDifferencesOnly(event.target.checked)} /> أظهر الاختلافات فقط</label></div>
          <div className="compare-matrix-wrap"><table className="compare-matrix"><thead><tr><th>العنصر</th>{matrix.products.map((product) => <th key={product.variant_id} className={product.variant_id === matrix.best_value_variant_id ? "best-value" : ""}>{collapseRepeatedName(product.canonical_name)}{product.variant_id === matrix.best_value_variant_id && <span>أفضل قيمة</span>}</th>)}</tr></thead><tbody>
            <tr><th>السعر</th>{matrix.products.map((product) => <td key={product.variant_id}>{money(product.lowest_final_cost)}</td>)}</tr>
            <tr><th>إجمالي التقسيط</th>{matrix.products.map((product) => <td key={product.variant_id}>{money(product.lowest_installment_total)}</td>)}</tr>
            <tr><th>الضمان</th>{matrix.products.map((product) => <td key={product.variant_id}>{product.warranty_months ? `${product.warranty_months} شهرًا` : "غير موضح"}</td>)}</tr>
            <tr><th>متاجر مؤكدة</th>{matrix.products.map((product) => <td key={product.variant_id}>{product.confirmed_store_count || 0}</td>)}</tr>
            <tr><th>القيمة مقابل السعر</th>{matrix.products.map((product) => <td key={product.variant_id}>{matrix.best_value_variant_id ? `${Math.round(product.value_score || 0)}/100` : "بيانات غير كافية"}</td>)}</tr>
            {rows.map((row) => <tr key={row.name}><th>{row.name}</th>{matrix.products.map((product) => <td key={product.variant_id}>{row.values[product.variant_id] || "—"}</td>)}</tr>)}
          </tbody></table></div>
          <div className="compare-share-actions"><button type="button" onClick={() => void shareImage()}>مشاركة المقارنة كصورة</button></div>
        </>}
        {message && <p role="status">{message}</p>}
      </div>}
    </aside>
  );
}
