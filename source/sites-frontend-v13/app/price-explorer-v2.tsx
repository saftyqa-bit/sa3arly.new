"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CatalogData, Product } from "./catalog-selectors";
import DecisionPanel from "./decision-panel";
import ProductCompareDrawer from "./product-compare-drawer";
import { modelNameParts } from "./display-names";
import {
  Sa3arlyBrand,
  Sa3arlyIcon,
  type Sa3arlyIconName,
} from "./brand-system";

export type PriceExplorerV2Props = {
  initialStats: {
    products: number;
    registryStores: number;
    selectedSectorStores: number;
  };
  initialVerifiedPublicMappings: number;
};

type SmartSearchItem = {
  variant_id: string;
  canonical_name: string;
  section?: string | null;
  product_type?: string | null;
  brand?: string | null;
  model?: string | null;
  variant_name?: string | null;
  ram_gb?: number | null;
  storage_gb?: number | null;
  color?: string | null;
  manufacturer_sku?: string | null;
  gtin?: string | null;
  image_url?: string | null;
  search_score?: number;
};

type SearchResponse = {
  query: string;
  normalized_query: string;
  items: SmartSearchItem[];
  suggestion?: string | null;
};

type RecentItem = {
  id: string;
  name: string;
  brand: string;
  model: string;
  variant: string;
  viewedAt: string;
};

type ModelGroup = {
  key: string;
  brand: string;
  model: string;
  type: string;
  section: string;
  variants: Product[];
  representative: Product;
};

const CATALOG_URL = "/catalog-data.json";
const MAX_COMPARE = 4;
const arabicDigits = "٠١٢٣٤٥٦٧٨٩";
const CATEGORY_PRIORITY = [
  "الموبايلات والاتصالات",
  "الكمبيوتر والشبكات",
  "التلفزيونات والصوتيات والتصوير",
  "الأجهزة المنزلية",
  "الألعاب الإلكترونية",
  "الأطفال والأمومة",
  "المطبخ والأدوات المنزلية",
  "الألعاب والهوايات",
];
const TELECOM_TYPE_PRIORITY = ["هواتف", "أجهزة تابلت", "ساعات ذكية", "أساور لياقة", "باور بانك", "راوترات منزلية"];
const PRODUCT_TYPE_ICONS: Record<string, Sa3arlyIconName> = {
  "هواتف": "mobile",
  "أجهزة تابلت": "mobile",
  "ساعات ذكية": "history",
  "أساور لياقة": "history",
  "باور بانك": "spark",
  "راوترات منزلية": "spark",
};

function priorityRank(value: string, priorities: string[]) {
  const index = priorities.indexOf(value);
  return index === -1 ? priorities.length : index;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("ar-EG-u-nu-latn").format(value);
}

function normalize(value: string) {
  let result = value.normalize("NFKC").toLocaleLowerCase("ar");
  arabicDigits.split("").forEach((digit, index) => { result = result.replaceAll(digit, String(index)); });
  return result
    .replace(/[ً-ٰٟ]/g, "")
    .replace(/[إأآ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/[-_/]+/g, " ")
    .replace(/جيجا\s*بايت|جيجابايت|جيجا/g, "gb")
    .replace(/\s+/g, " ")
    .trim();
}

function modelKey(product: Product) {
  return `${normalize(product.brand || "")}\u0000${normalize(product.model || product.name || "")}`;
}

function variantLabel(product: Product) {
  const specs = product.specs
    .filter((item) => item.name !== "اسم النسخة")
    .map((item) => item.value)
    .filter(Boolean);
  return specs.length ? specs.join(" · ") : product.variant || product.name;
}

function initials(value: string) {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function safeImage(product: Product | null) {
  if (!product) return null;
  const row = product as unknown as Record<string, unknown>;
  const raw = String(row.image_url || row.imageUrl || row.image || "");
  if (!raw) return null;
  try {
    const url = new URL(raw, window.location.origin);
    return url.protocol === "https:" || url.origin === window.location.origin ? url.toString() : null;
  } catch {
    return null;
  }
}

function ProductVisual({ product }: { product: Product | null }) {
  const [broken, setBroken] = useState(false);
  const image = !broken ? safeImage(product) : null;
  if (image && product) return <img className="product-real-image" src={image} alt={product.name} onError={() => setBroken(true)} />;
  return <div className="product-image-fallback" aria-hidden="true"><b>{product ? initials(product.brand || product.model || "س") : "س"}</b><span>{product?.type || "اختر منتجًا"}</span></div>;
}

function SearchVisual({ item }: { item: SmartSearchItem }) {
  if (item.image_url?.startsWith("https://")) {
    return <img className="product-real-image" src={item.image_url} alt="" />;
  }
  return <div className="product-image-fallback small" aria-hidden="true"><b>{initials(item.brand || item.model || "س")}</b><span>{item.product_type || "منتج"}</span></div>;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "") as T;
  } catch {
    return fallback;
  }
}

export default function PriceExplorerV2({ initialStats }: PriceExplorerV2Props) {
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const catalogRef = useRef<CatalogData | null>(null);
  const catalogPromise = useRef<Promise<CatalogData> | null>(null);
  const [catalogError, setCatalogError] = useState(false);
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [searchItems, setSearchItems] = useState<SmartSearchItem[]>([]);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [browseCategory, setBrowseCategory] = useState("");
  const [browseType, setBrowseType] = useState("");
  const [browseBrand, setBrowseBrand] = useState("");
  const [selectionMode, setSelectionMode] = useState<"search" | "browse">("search");
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<Product | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  const [toast, setToast] = useState("");
  const [systemIssue, setSystemIssue] = useState<string | null>(null);
  const [liveStats, setLiveStats] = useState({
    products: initialStats.products,
    registryStores: initialStats.registryStores,
    connectedStores: initialStats.selectedSectorStores,
    pricedStores: 0,
  });

  const ensureCatalog = useCallback(async () => {
    if (catalogRef.current) return catalogRef.current;
    if (!catalogPromise.current) {
      catalogPromise.current = fetch(CATALOG_URL, { cache: "force-cache" })
        .then((response) => {
          if (!response.ok) throw new Error("catalog unavailable");
          return response.json() as Promise<CatalogData>;
        });
    }
    try {
      const value = await catalogPromise.current;
      catalogRef.current = value;
      setCatalog(value);
      setCatalogError(false);
      return value;
    } catch (error) {
      setCatalogError(true);
      catalogPromise.current = null;
      throw error;
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const category = params.get("category") || "";
    const type = params.get("type") || "";
    const brand = params.get("brand") || "";
    const model = params.get("model") || "";
    const hasExplicitBrowseFilter = Boolean(category || type || brand || model);
    const restoredQuery = params.get("q") || (hasExplicitBrowseFilter ? model || brand || "" : sessionStorage.getItem("sa3arly-last-query") || "");
    setQuery(restoredQuery);
    setBrowseCategory(category);
    setBrowseType(type);
    setBrowseBrand(brand);
    setSelectionMode(hasExplicitBrowseFilter ? "browse" : "search");
    setCompareIds(readJson<string[]>("sa3arly-compare-products", []).slice(0, MAX_COMPARE));
    setRecent(readJson<RecentItem[]>("sa3arly-recent-products", []).slice(0, 8));
    const productId = params.get("product");
    if (productId || hasExplicitBrowseFilter || readJson<string[]>("sa3arly-compare-products", []).length) {
      void ensureCatalog().then((data) => {
        if (!productId) return;
        const product = data.products.find((candidate) => candidate.id === productId);
        if (product) {
          setSelectedModelKey(modelKey(product));
          setSelected(product);
        }
      }).catch(() => undefined);
    }
  }, [ensureCatalog]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/live/status", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("status unavailable");
        return response.json() as Promise<{
          products?: number;
          registry_stores?: number;
          connected_stores?: number;
          priced_stores?: number;
          visible_priced_stores?: number;
          latest_cash_update?: string | null;
        }>;
      })
      .then((status) => {
        if (cancelled) return;
        setLiveStats({
          products: Number.isFinite(status.products) ? Number(status.products) : initialStats.products,
          registryStores: Number.isFinite(status.registry_stores) ? Number(status.registry_stores) : initialStats.registryStores,
          connectedStores: Number.isFinite(status.connected_stores) ? Number(status.connected_stores) : initialStats.selectedSectorStores,
          pricedStores: Number.isFinite(status.visible_priced_stores)
            ? Number(status.visible_priced_stores)
            : Number.isFinite(status.priced_stores) ? Number(status.priced_stores) : 0,
        });
        if (!status.latest_cash_update) return;
        const age = Date.now() - new Date(status.latest_cash_update).getTime();
        if (Number.isFinite(age) && age > 30 * 3_600_000) setSystemIssue("تحديث الأسعار متأخر مؤقتًا. سنعرض فقط العروض التي اجتازت التحقق.");
      })
      .catch(() => { if (!cancelled) setSystemIssue("خدمة الأسعار الحية متأخرة مؤقتًا؛ البحث في دليل المنتجات ما زال متاحًا."); });
    return () => { cancelled = true; };
  }, [initialStats]);

  useEffect(() => {
    sessionStorage.setItem("sa3arly-last-query", query);
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set("q", query.trim()); else params.delete("q");
    if (selected) params.set("product", selected.id); else params.delete("product");
    const next = `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`;
    window.history.replaceState({ sa3arly: true }, "", next);
  }, [query, selected]);

  useEffect(() => {
    const clean = query.trim();
    if (clean.length < 2) {
      setSearchItems([]);
      setSuggestion(null);
      setSearchState("idle");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchState("loading");
      fetch(`/api/live/search?q=${encodeURIComponent(clean)}&limit=16`, { signal: controller.signal, cache: "no-store" })
        .then(async (response) => {
          const body = await response.json() as SearchResponse & { detail?: string };
          if (!response.ok) throw new Error(body.detail || "search unavailable");
          return body;
        })
        .then((body) => {
          setSearchItems(body.items);
          setSuggestion(body.suggestion || null);
          setSearchState("ready");
        })
        .catch(async (error) => {
          if (controller.signal.aborted) return;
          try {
            const data = await ensureCatalog();
            const tokens = normalize(clean).split(" ").filter(Boolean);
            const items = data.products
              .filter((product) => tokens.every((token) => normalize(`${product.brand} ${product.model} ${product.name} ${product.variant} ${variantLabel(product)}`).includes(token)))
              .slice(0, 16)
              .map<SmartSearchItem>((product) => ({ variant_id: product.id, canonical_name: product.name, section: product.section, product_type: product.type, brand: product.brand, model: product.model, variant_name: product.variant, search_score: 0.5 }));
            setSearchItems(items);
            setSuggestion(items[0]?.canonical_name || null);
            setSearchState("ready");
          } catch {
            setSearchState("error");
          }
          if (error instanceof Error && error.name === "AbortError") return;
        });
    }, 260);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [ensureCatalog, query]);

  const modelGroups = useMemo<ModelGroup[]>(() => {
    if (!catalog) return [];
    const groups = new Map<string, Product[]>();
    for (const product of catalog.products) {
      const key = modelKey(product);
      groups.set(key, [...(groups.get(key) || []), product]);
    }
    return [...groups.entries()].map(([key, variants]) => {
      variants.sort((left, right) => variantLabel(left).localeCompare(variantLabel(right), "ar", { numeric: true }));
      const representative = variants[0];
      return { key, brand: representative.brand, model: representative.model, type: representative.type, section: representative.section, variants, representative };
    });
  }, [catalog]);

  const selectedModel = useMemo(() => modelGroups.find((group) => group.key === selectedModelKey) || null, [modelGroups, selectedModelKey]);
  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    modelGroups.forEach((group) => counts.set(group.section, (counts.get(group.section) || 0) + 1));
    return counts;
  }, [modelGroups]);
  const categories = useMemo(() => [...categoryCounts.keys()].sort((a, b) => {
    const ranked = priorityRank(a, CATEGORY_PRIORITY) - priorityRank(b, CATEGORY_PRIORITY);
    return ranked || (categoryCounts.get(b) || 0) - (categoryCounts.get(a) || 0) || a.localeCompare(b, "ar");
  }), [categoryCounts]);
  const browseTypeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    modelGroups
      .filter((group) => !browseCategory || group.section === browseCategory)
      .forEach((group) => counts.set(group.type, (counts.get(group.type) || 0) + 1));
    return counts;
  }, [browseCategory, modelGroups]);
  const browseTypes = useMemo(() => [...browseTypeCounts.keys()].sort((a, b) => {
    if (browseCategory === "الموبايلات والاتصالات") {
      const ranked = priorityRank(a, TELECOM_TYPE_PRIORITY) - priorityRank(b, TELECOM_TYPE_PRIORITY);
      if (ranked) return ranked;
    }
    return (browseTypeCounts.get(b) || 0) - (browseTypeCounts.get(a) || 0) || a.localeCompare(b, "ar");
  }), [browseCategory, browseTypeCounts]);
  const browseBrands = useMemo(() => [...new Set(modelGroups.filter((group) => (!browseCategory || group.section === browseCategory) && (!browseType || group.type === browseType)).map((group) => group.brand).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ar")), [browseCategory, browseType, modelGroups]);
  const filteredBrowseGroups = useMemo(() => modelGroups.filter((group) =>
    (!browseCategory || group.section === browseCategory)
    && (!browseType || group.type === browseType)
    && (!browseBrand || normalize(group.brand) === normalize(browseBrand))
  ).sort((a, b) => {
    const sectionRank = priorityRank(a.section, CATEGORY_PRIORITY) - priorityRank(b.section, CATEGORY_PRIORITY);
    if (sectionRank) return sectionRank;
    if (browseCategory === "الموبايلات والاتصالات") {
      const typeRank = priorityRank(a.type, TELECOM_TYPE_PRIORITY) - priorityRank(b.type, TELECOM_TYPE_PRIORITY);
      if (typeRank) return typeRank;
    }
    return b.variants.length - a.variants.length
      || a.brand.localeCompare(b.brand, "ar")
      || a.model.localeCompare(b.model, "ar", { numeric: true });
  }), [browseBrand, browseCategory, browseType, modelGroups]);
  const browseGroups = useMemo(() => filteredBrowseGroups.slice(0, 48), [filteredBrowseGroups]);

  async function chooseSearchItem(item: SmartSearchItem) {
    try {
      const data = await ensureCatalog();
      const matched = data.products.find((candidate) => candidate.id === item.variant_id)
        || data.products.find((candidate) => normalize(candidate.brand) === normalize(item.brand || "") && normalize(candidate.model) === normalize(item.model || ""));
      if (!matched) throw new Error("product missing");
      const product = item.image_url ? ({ ...matched, image_url: item.image_url } as Product) : matched;
      setSelectedModelKey(modelKey(product));
      setQuery(`${product.brand} ${product.model}`.trim());
      chooseVariant(product);
    } catch {
      setToast("تعذر تحميل أسعار هذا المنتج مؤقتًا.");
    }
  }

  async function chooseModel(group: ModelGroup) {
    setSelectedModelKey(group.key);
    setSelected(null);
    setQuery(`${group.brand} ${group.model}`.trim());
    requestAnimationFrame(() => document.querySelector("#variant-step")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function chooseVariant(product: Product) {
    setSelected(product);
    const item: RecentItem = { id: product.id, name: product.name, brand: product.brand, model: product.model, variant: variantLabel(product), viewedAt: new Date().toISOString() };
    setRecent((current) => {
      const updated = [item, ...current.filter((candidate) => candidate.id !== item.id)].slice(0, 8);
      localStorage.setItem("sa3arly-recent-products", JSON.stringify(updated));
      return updated;
    });
    requestAnimationFrame(() => document.querySelector("#decision-center")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  async function selectVariantById(id: string) {
    try {
      const data = await ensureCatalog();
      const product = data.products.find((candidate) => candidate.id === id);
      if (!product) throw new Error("missing");
      setSelectedModelKey(modelKey(product));
      chooseVariant(product);
    } catch {
      setToast("البديل غير متاح في نسخة الدليل الحالية.");
    }
  }

  async function addToCompare(id: string) {
    try {
      await ensureCatalog();
    } catch {
      setToast("تعذر تجهيز المقارنة مؤقتًا.");
      return;
    }
    setCompareIds((current) => {
      if (current.includes(id)) {
        setToast("المنتج موجود بالفعل في المقارنة.");
        return current;
      }
      if (current.length >= MAX_COMPARE) {
        setToast("يمكن مقارنة أربعة منتجات كحد أقصى.");
        return current;
      }
      const updated = [...current, id];
      localStorage.setItem("sa3arly-compare-products", JSON.stringify(updated));
      setToast("تمت إضافة المنتج للمقارنة.");
      return updated;
    });
  }

  const searchProducts = useMemo(() => {
    const seen = new Set<string>();
    return searchItems.filter((item) => {
      if (seen.has(item.variant_id)) return false;
      seen.add(item.variant_id);
      return true;
    }).slice(0, 12);
  }, [searchItems]);

  function openSearchWorkspace() {
    setSelectionMode("search");
    requestAnimationFrame(() => document.querySelector("#selector")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function openCategory(category: string) {
    setSelectionMode("browse");
    setBrowseCategory(category);
    setBrowseType("");
    setBrowseBrand("");
    setSelectedModelKey(null);
    setSelected(null);
    void ensureCatalog();
    requestAnimationFrame(() => document.querySelector("#selector")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  return (
    <main className="price-page phase-two-page">
      <header className="price-hero">
        <div className="price-nav">
          <Link className="brand-lockup" href="/" aria-label="سعرلي — الرئيسية"><Sa3arlyBrand compact /></Link>
          <form className="header-product-search" role="search" onSubmit={(event) => { event.preventDefault(); openSearchWorkspace(); }}>
            <Sa3arlyIcon name="search" />
            <input
              id="header-product-search"
              aria-label="ابحث عن منتج"
              value={query}
              onChange={(event) => { setQuery(event.target.value); setSelected(null); setSelectedModelKey(null); setSelectionMode("search"); }}
              placeholder="ابحث في المنتجات والموديلات…"
              autoComplete="off"
            />
            <button type="submit">بحث</button>
            {query.trim().length >= 2 && !selected && <div className="header-search-results" role="listbox" aria-label="اقتراحات البحث">
              {searchState === "loading" && <p>جاري البحث…</p>}
              {searchState === "ready" && !searchProducts.length && <p>لم نجد منتجًا مطابقًا. جرّب اسمًا أقصر.</p>}
              {searchProducts.slice(0, 6).map((item) => <button key={item.variant_id} type="button" role="option" onClick={() => void chooseSearchItem(item)}><SearchVisual item={item} /><span><b><bdi dir="auto">{item.canonical_name}</bdi></b><small>{item.variant_name || item.product_type || item.section}</small></span><em>اعرض الأسعار</em></button>)}
              <button type="submit" className="show-all-search-results">عرض كل النتائج</button>
            </div>}
          </form>
          <nav className="header-account-links" aria-label="روابط رئيسية"><Link href="/stores">المتاجر</Link><Link href="/cash">أسعار الكاش</Link><Link href="/installments">التقسيط</Link></nav>
        </div>
        <nav className="quick-category-nav" aria-label="التصفح السريع">
          <button type="button" className="all-categories-button" onClick={() => { setSelectionMode("browse"); void ensureCatalog(); requestAnimationFrame(() => document.querySelector("#selector")?.scrollIntoView({ behavior: "smooth", block: "start" })); }}><Sa3arlyIcon name="browse" /> كل الفئات</button>
          {CATEGORY_PRIORITY.map((category) => <button key={category} type="button" onClick={() => openCategory(category)}>{category}</button>)}
          <Link href="/categories">المزيد</Link>
        </nav>

        <div className="minimal-home-intro">
          <div><span>مقارنة أسعار بدون إعلانات</span><h1>اختار المنتج وشوف كل أسعاره</h1></div>
          <p>اختر المنتج وشاهد أسعاره في كل المتاجر، مرتبة من الأرخص للأغلى مع الشحن.</p>
          <span className="visually-hidden">اعرض السعر مباشرة بدون إضافتها للمقارنة. مثال للبحث: سامسونج ايه ٥٥ ٢٥٦ جيجا.</span>
        </div>
      </header>

      <section className="comparison-workspace" id="selector">
        {systemIssue && selected && <div className="system-issue workspace-issue" role="status">{systemIssue}</div>}
        <ol className="comparison-progress" aria-label="خطوات معرفة السعر"><li className="active"><span>1</span><b>اختيار المنتج</b></li><li className={selectedModel ? "active" : ""}><span>2</span><b>تحديد النسخة</b></li><li className={selected ? "active" : ""}><span>3</span><b>أسعار المتاجر</b></li></ol>

        <section className="smart-product-search phase-two-search" aria-labelledby="smart-search-title">
          <div className="selector-intro">
            <div className="search-heading"><span>اختيار المنتج</span><h2 id="smart-search-title">كيف تحب تبدأ؟</h2><p>لن نختار منتجًا افتراضيًا أو نخلط بين نسختين مختلفتين.</p></div>
            <div className="selection-methods" role="tablist" aria-label="طريقة اختيار المنتج">
              <button type="button" role="tab" aria-selected={selectionMode === "search"} className={selectionMode === "search" ? "active" : ""} onClick={() => setSelectionMode("search")}><span aria-hidden="true"><Sa3arlyIcon name="search" /></span><b>اكتب اسم المنتج</b><small>لما تعرف الموديل</small></button>
              <button type="button" role="tab" aria-selected={selectionMode === "browse"} className={selectionMode === "browse" ? "active" : ""} onClick={() => { setSelectionMode("browse"); void ensureCatalog(); }}><span aria-hidden="true"><Sa3arlyIcon name="browse" /></span><b>اختَر خطوة بخطوة</b><small>فئة، نوع، ماركة، موديل</small></button>
            </div>
          </div>

          {selectionMode === "search" && <div className="search-flow single-search-flow" role="tabpanel">
            <div className="search-heading compact"><h3>{query.trim() ? <>نتائج البحث عن: <bdi dir="auto">{query.trim()}</bdi></> : "اكتب اسم المنتج أو الموديل أو SKU"}</h3><p>{query.trim() ? "اختر النتيجة الصحيحة لعرض النسخة والأسعار فورًا." : "استخدم خانة البحث الثابتة أعلى الصفحة؛ مثال: iPhone 17 Pro Max."}</p></div>
            {!query.trim() && <button type="button" className="focus-header-search" onClick={() => document.querySelector<HTMLInputElement>("#header-product-search")?.focus()}><Sa3arlyIcon name="search" /> ابدأ بالبحث عن منتج</button>}
            {suggestion && query.trim() && normalize(suggestion) !== normalize(query) && <button type="button" className="did-you-mean" onClick={() => setQuery(suggestion)}>هل تقصد: <b>{suggestion}</b>؟</button>}
            {query.trim().length >= 2 && !selected && <div className="instant-results" role="listbox" aria-label="نتائج البحث">
              {searchState === "loading" && <p>جاري مطابقة الاسم بالموديلات والنسخ…</p>}
              {searchState === "error" && <p>تعذر البحث الحي مؤقتًا. جرّب الاختيار خطوة بخطوة.</p>}
              {searchState === "ready" && !searchProducts.length && <p>لم نجد نتيجة مطابقة. جرّب رقم الموديل أو اسمًا أقصر.</p>}
              {searchProducts.map((item) => <article key={item.variant_id} className="smart-result-card"><button type="button" className="result-main-action" onClick={() => void chooseSearchItem(item)}><SearchVisual item={item} /><span><b><bdi dir="auto">{item.canonical_name || modelNameParts(item.brand, item.model || "")}</bdi></b><small>{item.variant_name || item.product_type || item.section}{item.storage_gb ? ` · ${item.storage_gb}GB` : ""}</small></span><em>اعرض الأسعار ←</em></button></article>)}
            </div>}
          </div>}

          {selectionMode === "browse" && <section className="browse-secondary browse-always" role="tabpanel" aria-label="اختيار المنتج خطوة بخطوة">
            <div className="browse-panel">
              <div className="browse-dropdown-heading">
                <div><span>اختيار متدرج</span><h3>حدد المنتج من القوائم</h3></div>
                <p>كل اختيار يجهز القائمة التالية، لحد ما توصل للموديل والنسخة الدقيقة.</p>
              </div>
              <div className="browse-dropdown-grid">
                <label><span>1. الفئة</span><select value={browseCategory} onChange={(event) => { setBrowseCategory(event.target.value); setBrowseType(""); setBrowseBrand(""); setSelectedModelKey(null); setSelected(null); }}><option value="">اختر الفئة</option>{categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
                <label><span>2. النوع</span><select value={browseType} disabled={!browseCategory} onChange={(event) => { setBrowseType(event.target.value); setBrowseBrand(""); setSelectedModelKey(null); setSelected(null); }}><option value="">{browseCategory ? "كل الأنواع" : "اختر الفئة أولًا"}</option>{browseTypes.map((type) => <option key={type} value={type}>{type}</option>)}</select></label>
                <label><span>3. الماركة</span><select value={browseBrand} disabled={!browseType} onChange={(event) => { setBrowseBrand(event.target.value); setSelectedModelKey(null); setSelected(null); }}><option value="">{browseType ? "اختر الماركة" : "اختر النوع أولًا"}</option>{browseBrands.map((brand) => <option key={brand} value={brand}>{brand}</option>)}</select></label>
                <label><span>4. الموديل</span><select value={selectedModelKey || ""} disabled={!browseBrand || !filteredBrowseGroups.length} onChange={(event) => { const group = filteredBrowseGroups.find((candidate) => candidate.key === event.target.value); if (group) void chooseModel(group); else { setSelectedModelKey(null); setSelected(null); } }}><option value="">{browseBrand ? "اختر الموديل" : "اختر الماركة أولًا"}</option>{filteredBrowseGroups.map((group) => <option key={group.key} value={group.key}>{modelNameParts(group.brand, group.model)} · {formatNumber(group.variants.length)} نسخ</option>)}</select></label>
              </div>
              <section className="browse-choice-step" aria-labelledby="browse-category-title">
                <header><span>1</span><div><h3 id="browse-category-title">اختَر الفئة</h3><p>ابدأ من نوع المنتج الذي تبحث عنه.</p></div></header>
                {!catalog && !catalogError && <p className="browse-loading">جاري تجهيز دليل المنتجات…</p>}
                {catalogError && <p className="browse-error">تعذر تحميل دليل الفئات مؤقتًا.</p>}
                {catalog && <div className="browse-category-list" role="group" aria-label="مجموعات المنتجات">
                  {categories.map((category) => <button key={category} type="button" className={browseCategory === category ? "active" : ""} aria-pressed={browseCategory === category} onClick={() => { setBrowseCategory(category); setBrowseType(""); setBrowseBrand(""); }}><b>{category}</b><small>{formatNumber(categoryCounts.get(category) || 0)} موديل</small></button>)}
                </div>}
              </section>

              {browseCategory && <section className="browse-choice-step browse-type-step" aria-labelledby="browse-type-title">
                <header><span>2</span><div><h3 id="browse-type-title">اختَر النوع</h3><p>الأنواع المتاحة داخل {browseCategory}.</p></div></header>
                <div className="browse-type-grid" role="group" aria-label="أنواع المنتجات">
                  {browseTypes.map((type) => {
                    const icon = PRODUCT_TYPE_ICONS[type];
                    return <button key={type} type="button" className={browseType === type ? "active" : ""} aria-pressed={browseType === type} onClick={() => { setBrowseType(type); setBrowseBrand(""); }}><span className="browse-type-icon" aria-hidden="true">{icon ? <Sa3arlyIcon name={icon} /> : type.slice(0, 1)}</span><span><b>{type}</b><small>{formatNumber(browseTypeCounts.get(type) || 0)} موديل</small></span></button>;
                  })}
                  <button type="button" className={`all-types ${!browseType ? "active" : ""}`.trim()} aria-pressed={!browseType} onClick={() => { setBrowseType(""); setBrowseBrand(""); }}><span className="browse-type-icon" aria-hidden="true"><Sa3arlyIcon name="browse" /></span><span><b>كل المنتجات</b><small>{formatNumber([...browseTypeCounts.values()].reduce((total, count) => total + count, 0))} موديل</small></span></button>
                </div>
              </section>}

              {browseCategory && <section className="browse-choice-step browse-brand-step" aria-labelledby="browse-brand-title">
                <header><span>3</span><div><h3 id="browse-brand-title">اختَر الماركة <small>اختياري</small></h3><p>اتركها على «كل الماركات» لمشاهدة كل الموديلات.</p></div></header>
                <label><span>الماركة</span><select value={browseBrand} onChange={(event) => setBrowseBrand(event.target.value)}><option value="">كل الماركات</option>{browseBrands.map((brand) => <option key={brand}>{brand}</option>)}</select></label>
              </section>}

              {browseBrand && <div className="browse-result-bar"><div><b>{browseType}</b><small>{browseBrand}</small></div><span>نعرض {formatNumber(Math.min(48, filteredBrowseGroups.length))} من {formatNumber(filteredBrowseGroups.length)} موديل</span></div>}
              {browseBrand && !browseGroups.length && <p className="browse-empty">لا توجد موديلات مطابقة لهذه الاختيارات حاليًا.</p>}
              {browseBrand && <div className="browse-model-grid">{browseGroups.map((group) => <article key={group.key}><button type="button" onClick={() => void chooseModel(group)}><ProductVisual product={group.representative} /><span className="browse-model-copy"><b>{modelNameParts(group.brand, group.model)}</b><small>{group.type} · {formatNumber(group.variants.length)} نسخ</small><em>اختر النسخة واعرض الأسعار ←</em></span></button></article>)}</div>}
            </div>
          </section>}
        </section>

        {selectedModel && <section className="variant-step" id="variant-step">
          <div className="variant-step-copy">
            <div className="search-heading"><span>الخطوة 2</span><h2>حدد النسخة الدقيقة</h2><p>اختيار النسخة يعرض أسعارها فورًا؛ لا نخلط بين RAM أو التخزين أو اللون أو SKU مختلف.</p></div>
            <div className="selected-model-summary"><ProductVisual product={selectedModel.representative} /><div><b>{modelNameParts(selectedModel.brand, selectedModel.model)}</b><small>{selectedModel.type} · {formatNumber(selectedModel.variants.length)} نسخ متاحة</small></div></div>
          </div>
          <label className="variant-select-control"><span>5. النسخة والمواصفات</span><select value={selected?.id || ""} onChange={(event) => { const variant = selectedModel.variants.find((candidate) => candidate.id === event.target.value); if (variant) chooseVariant(variant); }}><option value="">اختر السعة والذاكرة واللون</option>{selectedModel.variants.map((variant) => <option key={variant.id} value={variant.id}>{variantLabel(variant)}{variant.mappedStores ? ` · ${formatNumber(variant.mappedStores)} متاجر` : ""}</option>)}</select><small>بعد الاختيار سيظهر جدول المتاجر مباشرة من الأرخص للأغلى.</small></label>
          <div className="variant-options compact-variant-options">{selectedModel.variants.slice(0, 8).map((variant) => <article key={variant.id} className={selected?.id === variant.id ? "active" : ""}><button type="button" onClick={() => chooseVariant(variant)}><b>{variantLabel(variant)}</b><small>{variant.mappedStores ? `${formatNumber(variant.mappedStores)} متاجر مرتبطة` : "جاري توسيع التغطية"}</small><em>{selected?.id === variant.id ? "الأسعار معروضة بالأسفل" : "اعرض الأسعار ←"}</em></button></article>)}</div>
        </section>}

        {!selectedModel && <section className="selection-promise" aria-label="ما الذي سيظهر بعد الاختيار"><div><span>بعد اختيار النسخة</span><h3>كل ما تحتاجه لاتخاذ القرار سيظهر هنا</h3></div><ul><li><b>أسعار كل المتاجر</b><small>مرتبة من الأرخص للأغلى</small></li><li><b>الشحن والتكلفة النهائية</b><small>مع توضيح البيانات غير المعروفة</small></li><li><b>التوفر والضمان</b><small>ووقت آخر تحقق من السعر</small></li></ul></section>}

        {selected && <DecisionPanel product={selected} onAddToCompare={(id) => void addToCompare(id)} onSelectVariant={(id) => void selectVariantById(id)} />}
      </section>

      {recent.length > 0 && <section className="recently-viewed"><div><span>شاهدته مؤخرًا</span><h2>ارجع لأسعاره بسرعة</h2></div><div className="recent-grid">{recent.map((item) => <article key={item.id}><button type="button" onClick={() => void selectVariantById(item.id)}><b>{item.brand} {item.model}</b><small>{item.variant}</small><em>اعرض أسعار المتاجر ←</em></button></article>)}</div></section>}

      <section className="readiness-strip" aria-label="حالة تغطية سعرلي"><div><span>{formatNumber(liveStats.products)}</span><small>نسخة منتج</small></div><div><span>{formatNumber(liveStats.registryStores)}</span><small>متجرًا مسجلًا</small></div><div><span>{formatNumber(liveStats.connectedStores)}</span><small>متجرًا مرتبطًا بمنتجات</small></div><div><span>{formatNumber(liveStats.pricedStores)}</span><small>متاجر بأسعار حية الآن</small></div></section>
      <section className="seo-discovery"><div><span>دليل سعرلي</span><h2>تصفح المنتجات والأسعار حسب الفئة</h2><p>صفحات عامة قابلة للمشاركة لمحركات البحث والمستخدمين.</p></div><nav><Link href="/cash">منتجات لها سعر كاش</Link><Link href="/installments">خطط التقسيط</Link><Link href="/categories">كل الفئات</Link><Link href="/stores">دليل المتاجر</Link></nav></section>

      {catalog && <ProductCompareDrawer selectedIds={compareIds} catalogProducts={catalog.products} onRemove={(id) => setCompareIds((current) => current.filter((item) => item !== id))} onClear={() => setCompareIds([])} />}
      {toast && <button type="button" className="price-toast" role="status" onClick={() => setToast("")}>{toast}</button>}
    </main>
  );
}
