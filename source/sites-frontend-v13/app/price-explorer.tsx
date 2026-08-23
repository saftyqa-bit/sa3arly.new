"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CatalogData, Product } from "./catalog-selectors";
import { isVerifiedPresence } from "./verification";

type CatalogPresence = {
  mappingId: string;
  storeId: string;
  storeName: string;
  entityType: string;
  presenceStatus: string;
  sourceUrl: string;
  linkType: string;
  matchConfidence: string;
  reviewStatus: string;
  cashStatus: string;
  cashLabel: string;
  installmentStatus: string;
  installmentLabel: string;
};

type CashOffer = {
  offer_id: string;
  store_name: string;
  seller_name?: string | null;
  store_logo_url?: string | null;
  cash_price?: number | null;
  old_price?: number | null;
  shipping_cost?: number | null;
  shipping_cost_known?: boolean;
  comparable_total?: number | null;
  availability?: string | null;
  source_url?: string | null;
  currency?: string | null;
  last_success_at?: string | null;
  verified?: boolean | null;
};

type InstallmentPlan = {
  plan_id: string;
  store_name: string;
  store_logo_url?: string | null;
  provider_name?: string | null;
  bank_or_card?: string | null;
  months?: number | null;
  periodic_payment?: number | null;
  down_payment?: number | null;
  admin_fees?: number | null;
  normalized_total?: number | null;
  total_published?: number | null;
  total_calculated?: number | null;
  interest_free?: boolean | null;
  source_url?: string | null;
  currency?: string | null;
  last_success_at?: string | null;
  verified?: boolean | null;
};

type Comparison = {
  product: Product & {
    canonical_name?: string;
    variant_name?: string;
    image_url?: string | null;
  };
  cash_offers: CashOffer[];
  installment_plans: InstallmentPlan[];
  last_known_cash_price?: number | null;
  last_known_cash_price_at?: string | null;
};

type LiveStats = Partial<{
  latest_cash_update: string | null;
}>;

type ModelGroup = {
  key: string;
  brand: string;
  model: string;
  section: string;
  category: string;
  type: string;
  variants: Product[];
  representative: Product;
};

export type PriceExplorerProps = {
  initialStats: {
    products: number;
    registryStores: number;
    selectedSectorStores: number;
  };
  initialVerifiedPublicMappings: number;
};

const CATALOG_URL = "/catalog-data.json";
const MAX_RESULTS = 8;

const availabilityLabels: Record<string, string> = {
  available: "متوفر",
  limited: "كمية محدودة",
  preorder: "طلب مسبق",
  out_of_stock: "غير متوفر",
  unknown: "التوفر غير مؤكد",
};

function formatNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn").format(value);
}

function formatMoney(value: number | null | undefined, currency = "EGP") {
  if (value == null || !Number.isFinite(value) || value <= 0) return "—";
  return new Intl.NumberFormat("ar-EG-u-nu-latn", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

function formatDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function safeUrl(value: unknown) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  if (raw.startsWith("/")) return raw;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function normalizeSearch(value: string) {
  return value
    .normalize("NFKC")
    .replace(/[ً-ٰٟ]/g, "")
    .replace(/[إأآا]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .toLocaleLowerCase("ar")
    .replace(/\s+/g, " ")
    .trim();
}

function variantLabel(product: Product) {
  const values = product.specs
    .filter((item) => item.name !== "اسم النسخة")
    .map((item) => item.value)
    .filter(Boolean);
  return values.length ? values.join(" · ") : product.variant || product.name;
}

function productImage(product: Product | null) {
  if (!product) return null;
  const record = product as unknown as Record<string, unknown>;
  return safeUrl(record.image_url ?? record.imageUrl ?? record.image);
}

function modelKey(product: Product) {
  return `${product.brand}\u0000${product.model}`;
}

function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function installmentTotal(plan: InstallmentPlan) {
  return plan.normalized_total ?? plan.total_published ?? plan.total_calculated ?? null;
}

function ProductVisual({ product }: { product: Product | null }) {
  const image = productImage(product);
  if (image && product) {
    return <img className="product-real-image" src={image} alt={product.name} />;
  }
  return (
    <div className="product-image-fallback" aria-hidden="true">
      <b>{product ? initials(product.brand || product.model) : "س"}</b>
      <span>{product?.type || "اختر منتجًا"}</span>
    </div>
  );
}

function StoreMark({ name, logo }: { name: string; logo?: string | null }) {
  const source = safeUrl(logo);
  return source ? (
    <img className="store-real-logo" src={source} alt={`شعار ${name}`} />
  ) : (
    <span className="store-logo-fallback" aria-hidden="true">{initials(name)}</span>
  );
}

function StepProgress({ step }: { step: 1 | 2 | 3 }) {
  return (
    <ol className="comparison-progress" aria-label="خطوات مقارنة السعر">
      {["اختيار المنتج", "تحديد النسخة", "مقارنة الأسعار"].map((label, index) => {
        const number = (index + 1) as 1 | 2 | 3;
        return (
          <li key={label} className={number <= step ? "active" : ""}>
            <span>{number}</span>
            <b>{label}</b>
          </li>
        );
      })}
    </ol>
  );
}

export default function PriceExplorer({
  initialStats,
  initialVerifiedPublicMappings,
}: PriceExplorerProps) {
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "error">("loading");
  const [query, setQuery] = useState("");
  const [browseCategory, setBrowseCategory] = useState("");
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<Product | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparisonState, setComparisonState] = useState<"idle" | "loading" | "live" | "offline">("idle");
  const [activeTab, setActiveTab] = useState<"cash" | "installment">("cash");
  const [systemIssue, setSystemIssue] = useState<string | null>(null);
  const [liveStats, setLiveStats] = useState<LiveStats | null>(null);
  const [toast, setToast] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch(CATALOG_URL)
      .then((response) => {
        if (!response.ok) throw new Error("catalog unavailable");
        return response.json() as Promise<CatalogData>;
      })
      .then((value) => {
        if (cancelled) return;
        setCatalog(value);
        setCatalogState("ready");
      })
      .catch(() => {
        if (!cancelled) setCatalogState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/live/status", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("status unavailable");
        return (await response.json()) as LiveStats;
      })
      .then((status) => {
        if (cancelled) return;
        setLiveStats(status);
        const latest = status.latest_cash_update ? new Date(status.latest_cash_update) : null;
        if (latest && !Number.isNaN(latest.getTime())) {
          const hours = (Date.now() - latest.getTime()) / 3_600_000;
          if (hours > 30) {
            setSystemIssue(`تحديث الأسعار متأخر مؤقتًا — آخر تحديث ناجح ${formatDate(status.latest_cash_update) ?? "غير متاح"}.`);
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSystemIssue("تحديث الأسعار متأخر مؤقتًا. يمكنك تصفح المنتجات وسنُظهر الأسعار المؤكدة فقط عند عودة الخدمة.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const modelGroups = useMemo<ModelGroup[]>(() => {
    if (!catalog) return [];
    const groups = new Map<string, Product[]>();
    for (const product of catalog.products) {
      const key = modelKey(product);
      groups.set(key, [...(groups.get(key) ?? []), product]);
    }
    return [...groups.entries()].map(([key, variants]) => {
      variants.sort((left, right) => variantLabel(left).localeCompare(variantLabel(right), "ar", { numeric: true }));
      const representative = variants[0];
      return {
        key,
        brand: representative.brand,
        model: representative.model,
        section: representative.section,
        category: representative.category,
        type: representative.type,
        variants,
        representative,
      };
    });
  }, [catalog]);

  const categories = useMemo(() => {
    const values = new Set<string>();
    for (const group of modelGroups) values.add(group.section || group.category || group.type);
    return [...values].filter(Boolean).sort((a, b) => a.localeCompare(b, "ar"));
  }, [modelGroups]);

  const results = useMemo(() => {
    const normalized = normalizeSearch(query);
    const tokens = normalized.split(" ").filter(Boolean);
    return modelGroups
      .filter((group) => !browseCategory || group.section === browseCategory)
      .flatMap((group) => {
        const haystack = normalizeSearch(`${group.brand} ${group.model} ${group.type} ${group.category}`);
        if (tokens.length && !tokens.every((token) => haystack.includes(token))) return [];
        if (!tokens.length && !browseCategory) return [];
        const rank = haystack.startsWith(normalized) ? 0 : haystack.includes(normalized) ? 1 : 2;
        return [{ group, rank }];
      })
      .sort((a, b) => a.rank - b.rank || a.group.model.localeCompare(b.group.model, "ar", { numeric: true }))
      .slice(0, MAX_RESULTS)
      .map((item) => item.group);
  }, [browseCategory, modelGroups, query]);

  const selectedModel = useMemo(
    () => modelGroups.find((group) => group.key === selectedModelKey) ?? null,
    [modelGroups, selectedModelKey],
  );

  const loadComparison = useCallback(async (product: Product) => {
    setSelected(product);
    setComparison(null);
    setComparisonState("loading");
    try {
      const response = await fetch(`/api/live/products/${encodeURIComponent(product.id)}/comparison`, { cache: "no-store" });
      if (!response.ok) throw new Error("comparison unavailable");
      setComparison((await response.json()) as Comparison);
      setComparisonState("live");
    } catch {
      setComparisonState("offline");
    }
    requestAnimationFrame(() => {
      document.querySelector("#comparison-results")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  useEffect(() => {
    if (!catalog) return;
    const productId = new URLSearchParams(window.location.search).get("product");
    if (!productId) return;
    const product = catalog.products.find((candidate) => candidate.id === productId);
    if (!product) return;
    setSelectedModelKey(modelKey(product));
    void loadComparison(product);
  }, [catalog, loadComparison]);

  const cashOffers = useMemo(
    () => [...(comparison?.cash_offers ?? [])].sort((a, b) => (a.comparable_total ?? a.cash_price ?? Number.MAX_SAFE_INTEGER) - (b.comparable_total ?? b.cash_price ?? Number.MAX_SAFE_INTEGER)),
    [comparison],
  );
  const installmentPlans = useMemo(
    () => [...(comparison?.installment_plans ?? [])].sort((a, b) => (installmentTotal(a) ?? Number.MAX_SAFE_INTEGER) - (installmentTotal(b) ?? Number.MAX_SAFE_INTEGER)),
    [comparison],
  );

  const selectedPresence = useMemo(() => {
    if (!catalog || !selected) return [];
    const rows = (catalog.presence as unknown as Record<string, CatalogPresence[]>)[selected.id] ?? [];
    return rows.filter((store) => isVerifiedPresence(selected, store));
  }, [catalog, selected]);

  const sameModelStores = useMemo(() => {
    if (!catalog || !selectedModel) return [];
    const seen = new Map<string, CatalogPresence>();
    const presence = catalog.presence as unknown as Record<string, CatalogPresence[]>;
    for (const variant of selectedModel.variants) {
      for (const store of presence[variant.id] ?? []) {
        if (isVerifiedPresence(variant, store)) seen.set(store.storeId, store);
      }
    }
    return [...seen.values()];
  }, [catalog, selectedModel]);

  const latestUpdate = useMemo(() => {
    const dates = cashOffers.map((offer) => offer.last_success_at).filter((value): value is string => Boolean(value));
    dates.push(...installmentPlans.map((plan) => plan.last_success_at).filter((value): value is string => Boolean(value)));
    return dates.sort().at(-1) ?? liveStats?.latest_cash_update ?? null;
  }, [cashOffers, installmentPlans, liveStats]);

  const step: 1 | 2 | 3 = selected ? 3 : selectedModel ? 2 : 1;
  const noCurrentOffers = Boolean(
    selected &&
      comparisonState !== "loading" &&
      cashOffers.length === 0 &&
      installmentPlans.length === 0,
  );

  function chooseModel(group: ModelGroup) {
    setSelectedModelKey(group.key);
    setSelected(null);
    setComparison(null);
    setComparisonState("idle");
    setQuery(`${group.brand} ${group.model}`.trim());
  }

  function saveForAlert() {
    if (!selected) return;
    localStorage.setItem(`sa3arly-price-alert:${selected.id}`, JSON.stringify({ productId: selected.id, savedAt: new Date().toISOString() }));
    setToast("تم حفظ النسخة على جهازك. الإرسال التلقائي سيبدأ بعد ربط خدمة الإشعارات.");
  }

  return (
    <main className="price-page">
      <header className="price-hero">
        <Link className="brand-lockup" href="/" aria-label="سعرلي — الرئيسية">
          <span>س</span><b>سعرلي</b>
        </Link>
        <nav aria-label="روابط رئيسية">
          <Link href="/cash">الكاش</Link>
          <Link href="/installments">التقسيط</Link>
          <Link href="/categories">الفئات</Link>
        </nav>
        <div className="hero-copy">
          <span>قارن قبل ما تشتري</span>
          <h1>دور على المنتج اللي عايز تقارن سعره</h1>
          <p>اكتب اسم المنتج أو الموديل، اختر النسخة الدقيقة، ثم شاهد الأسعار المؤكدة من المتاجر.</p>
        </div>
        {systemIssue && <div className="system-issue" role="status">{systemIssue}</div>}
      </header>

      <section className="comparison-workspace" id="selector">
        <StepProgress step={step} />
        <section className="smart-product-search" aria-labelledby="smart-search-title">
          <div className="search-heading">
            <span>الخطوة 1</span>
            <h2 id="smart-search-title">اكتب اسم المنتج أو الموديل</h2>
          </div>
          <label className="smart-search-input">
            <span aria-hidden="true">⌕</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="مثال: iPhone 16 Pro أو Samsung S25"
              autoComplete="off"
            />
          </label>
          <details className="browse-secondary">
            <summary>تصفح حسب الفئة</summary>
            <label>
              <span>الفئة الرئيسية</span>
              <select value={browseCategory} onChange={(event) => setBrowseCategory(event.target.value)}>
                <option value="">اختر فئة</option>
                {categories.map((category) => <option key={category}>{category}</option>)}
              </select>
            </label>
          </details>

          {(query.trim() || browseCategory) && (
            <div className="instant-results" role="listbox" aria-label="نتائج البحث">
              {catalogState === "loading" && <p>جاري تجهيز دليل المنتجات…</p>}
              {catalogState === "error" && <p>تعذر تحميل دليل المنتجات مؤقتًا.</p>}
              {catalogState === "ready" && results.length === 0 && <p>لا يوجد موديل مطابق. جرّب كتابة اسم أقصر أو اختر فئة.</p>}
              {results.map((group) => (
                <button type="button" key={group.key} onClick={() => chooseModel(group)}>
                  <ProductVisual product={group.representative} />
                  <span>
                    <b><bdi dir="auto">{group.brand} {group.model}</bdi></b>
                    <small>{group.type} · {formatNumber(group.variants.length)} نسخ متاحة</small>
                  </span>
                  <em>اختر الموديل ←</em>
                </button>
              ))}
            </div>
          )}
        </section>

        {selectedModel && (
          <section className="variant-step" aria-labelledby="variant-step-title">
            <div className="search-heading"><span>الخطوة 2</span><h2 id="variant-step-title">حدد النسخة الدقيقة</h2></div>
            <div className="selected-model-summary">
              <ProductVisual product={selectedModel.representative} />
              <div><b>{selectedModel.brand} {selectedModel.model}</b><small>{selectedModel.type}</small></div>
            </div>
            <div className="variant-options">
              {selectedModel.variants.map((variant) => (
                <button
                  type="button"
                  key={variant.id}
                  className={selected?.id === variant.id ? "active" : ""}
                  onClick={() => void loadComparison(variant)}
                >
                  <b>{variantLabel(variant)}</b>
                  <small>{variant.mappedStores ? `${formatNumber(variant.mappedStores)} متاجر مرتبطة` : "جاري توسيع التغطية"}</small>
                </button>
              ))}
            </div>
          </section>
        )}

        {!selected && !selectedModel && (
          <section className="selection-empty">
            <ProductVisual product={null} />
            <div><b>ابدأ بالبحث عن منتج</b><p>لن نختار لك منتجًا بلا عروض، ولن نعرض أرقامًا صفرية على أنها أسعار.</p></div>
          </section>
        )}

        {selected && (
          <section className="comparison-results" id="comparison-results" tabIndex={-1}>
            <div className="result-product-heading">
              <ProductVisual product={selected} />
              <div><span>الخطوة 3</span><h2>{selected.name}</h2><p>{variantLabel(selected)}</p></div>
              <button type="button" onClick={saveForAlert}>احفظ للتنبيه</button>
            </div>

            <div className="comparison-tabs" role="tablist" aria-label="نوع المقارنة">
              <button type="button" className={activeTab === "cash" ? "active" : ""} onClick={() => setActiveTab("cash")}>الكاش <span>{cashOffers.length}</span></button>
              <button type="button" className={activeTab === "installment" ? "active" : ""} onClick={() => setActiveTab("installment")}>التقسيط <span>{installmentPlans.length}</span></button>
            </div>

            {comparisonState === "loading" && <div className="offers-loading">جاري تحميل الأسعار المؤكدة…</div>}

            {noCurrentOffers && (
              <div className="useful-empty-state">
                <h3>لم نجد سعرًا حيًا لهذه النسخة الآن.</h3>
                {comparison?.last_known_cash_price ? (
                  <p>آخر سعر موثّق: <b dir="ltr">{formatMoney(comparison.last_known_cash_price)}</b>{formatDate(comparison.last_known_cash_price_at) ? ` — ${formatDate(comparison.last_known_cash_price_at)}` : ""}</p>
                ) : <p>لن نعرض سعرًا قديمًا أو رقمًا غير مؤكد على أنه عرض حالي.</p>}
                <div className="empty-actions">
                  <button type="button" onClick={saveForAlert}>احفظ النسخة للتنبيه</button>
                  {(selectedModel?.variants ?? []).filter((variant) => variant.id !== selected.id).slice(0, 3).map((variant) => (
                    <button type="button" key={variant.id} onClick={() => void loadComparison(variant)}>اعرض {variantLabel(variant)}</button>
                  ))}
                </div>
                {sameModelStores.length > 0 && (
                  <div className="same-model-stores">
                    <b>متاجر تبيع نفس الموديل</b>
                    <div>{sameModelStores.slice(0, 8).map((store) => <span key={store.storeId}>{store.storeName}</span>)}</div>
                  </div>
                )}
              </div>
            )}

            {comparisonState !== "loading" && activeTab === "cash" && cashOffers.length > 0 && (
              <div className="mobile-offer-cards">
                {cashOffers.map((offer, index) => {
                  const source = safeUrl(offer.source_url);
                  const shipping = offer.shipping_cost_known
                    ? (offer.shipping_cost ?? 0) === 0 ? "شحن مجاني" : formatMoney(offer.shipping_cost, offer.currency || "EGP")
                    : "الشحن غير معلوم";
                  return (
                    <article key={offer.offer_id} className={index === 0 ? "best" : ""}>
                      <header>
                        <StoreMark name={offer.store_name} logo={offer.store_logo_url} />
                        <div><b>{offer.store_name}</b><small>{offer.seller_name || "المتجر مباشرة"}</small></div>
                        <span className="verified-offer">✓ عرض موثّق</span>
                      </header>
                      <div className="offer-price"><b dir="ltr">{formatMoney(offer.comparable_total ?? offer.cash_price, offer.currency || "EGP")}</b>{index === 0 && <span>أفضل سعر</span>}</div>
                      <dl>
                        <div><dt>السعر</dt><dd dir="ltr">{formatMoney(offer.cash_price, offer.currency || "EGP")}</dd></div>
                        <div><dt>الشحن</dt><dd>{shipping}</dd></div>
                        <div><dt>التوفر</dt><dd>{availabilityLabels[offer.availability || "unknown"]}</dd></div>
                        {formatDate(offer.last_success_at) && <div><dt>آخر تحديث</dt><dd>{formatDate(offer.last_success_at)}</dd></div>}
                      </dl>
                      {source ? <a href={source} target="_blank" rel="noreferrer">اذهب للمتجر ↗</a> : <span className="disabled-store-link">رابط المتجر قيد المراجعة</span>}
                    </article>
                  );
                })}
              </div>
            )}

            {comparisonState !== "loading" && activeTab === "installment" && installmentPlans.length > 0 && (
              <div className="mobile-offer-cards">
                {installmentPlans.map((plan, index) => {
                  const source = safeUrl(plan.source_url);
                  return (
                    <article key={plan.plan_id} className={index === 0 ? "best" : ""}>
                      <header>
                        <StoreMark name={plan.store_name} logo={plan.store_logo_url} />
                        <div><b>{plan.store_name}</b><small>{plan.provider_name || plan.bank_or_card || "خطة المتجر"}</small></div>
                        <span className="verified-offer">✓ خطة موثّقة</span>
                      </header>
                      <div className="offer-price"><b dir="ltr">{formatMoney(plan.periodic_payment, plan.currency || "EGP")}</b><span>قسط شهري</span></div>
                      <dl>
                        <div><dt>المدة</dt><dd>{plan.months ? `${plan.months} شهرًا` : "غير منشورة"}</dd></div>
                        <div><dt>المقدم</dt><dd dir="ltr">{formatMoney(plan.down_payment, plan.currency || "EGP")}</dd></div>
                        <div><dt>الإجمالي</dt><dd dir="ltr">{formatMoney(installmentTotal(plan), plan.currency || "EGP")}</dd></div>
                        {formatDate(plan.last_success_at) && <div><dt>آخر تحديث</dt><dd>{formatDate(plan.last_success_at)}</dd></div>}
                      </dl>
                      {source ? <a href={source} target="_blank" rel="noreferrer">اذهب للمتجر ↗</a> : <span className="disabled-store-link">رابط الخطة قيد المراجعة</span>}
                    </article>
                  );
                })}
              </div>
            )}

            {selectedPresence.length > 0 && (
              <details className="known-stores-details">
                <summary>اعرض المتاجر التي ثبت لديها نفس النسخة ({selectedPresence.length})</summary>
                <div>{selectedPresence.map((store) => <span key={store.mappingId}>{store.storeName}</span>)}</div>
              </details>
            )}

            {latestUpdate && (
              <footer className="freshness-footer">آخر تحديث ناجح: {formatDate(latestUpdate)} · يتم التحقق من الأسعار دوريًا، وقد يختلف الشحن حسب العنوان.</footer>
            )}
          </section>
        )}
      </section>

      <section className="readiness-strip" aria-label="نطاق دليل سعرلي">
        <div><span>{formatNumber(initialStats.products)}</span><small>نسخة منتج</small></div>
        <div><span>{formatNumber(initialStats.registryStores)}</span><small>متجرًا في الدليل</small></div>
        <div><span>{formatNumber(initialStats.selectedSectorStores)}</span><small>مصدرًا للقطاعات المختارة</small></div>
        <div><span>{formatNumber(initialVerifiedPublicMappings)}</span><small>ربطًا موثّقًا</small></div>
      </section>

      <section className="seo-discovery">
        <div><span>دليل سعرلي</span><h2>تصفح المنتجات والأسعار حسب الفئة</h2><p>يمكنك أيضًا الدخول إلى صفحات الكاش والتقسيط والفئات والمتاجر.</p></div>
        <nav><Link href="/cash">منتجات لها سعر كاش</Link><Link href="/installments">خطط التقسيط</Link><Link href="/categories">كل الفئات</Link><Link href="/stores">دليل المتاجر</Link></nav>
      </section>

      {toast && <div className="price-toast" role="status" onClick={() => setToast("")}>{toast}</div>}
    </main>
  );
}