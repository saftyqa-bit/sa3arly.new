import { cloudRunAuthorizationHeaders } from "./cloud-run-auth";

export type LiveCashOffer = {
  offer_id: string;
  store_name: string;
  seller_name?: string | null;
  cash_price: number;
  old_price?: number | null;
  shipping_cost?: number | null;
  shipping_cost_known?: boolean;
  comparable_total?: number | null;
  availability?: string | null;
  computed_freshness?: string | null;
  source_url?: string | null;
  currency: "EGP";
  last_success_at?: string | null;
  eligible_for_ranking?: boolean;
  anomaly_status?: "clear" | "review";
  anomaly_reasons?: string[];
};

export type LiveInstallmentPlan = {
  plan_id: string;
  store_name: string;
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
  starting_from_only?: boolean;
  source_url?: string | null;
  currency: "EGP";
  eligibility?: string | null;
  last_success_at?: string | null;
};

export type LiveComparison = {
  cashOffers: LiveCashOffer[];
  installmentPlans: LiveInstallmentPlan[];
  productImageUrl: string | null;
};

export type ListingPriceHistory = {
  lowest30d: number | null;
  lowest90d: number | null;
  average90d: number | null;
  highest90d: number | null;
  sparkline: Array<{ date: string; price: number }>;
};

export type PricedProductSummary = {
  variantId: string;
  canonicalName: string;
  section: string;
  productType: string | null;
  brand: string;
  model: string;
  variantName: string | null;
  lowestCashPrice: number | null;
  lowestConfirmedCashPrice: number | null;
  lowestDeliveredTotal: number | null;
  lowestFinalCost: number | null;
  cashOfferCount: number;
  confirmedCashOfferCount: number;
  reviewCashOfferCount: number;
  cashPriceReviewRequired: boolean;
  installmentPlanCount: number;
  confirmedInstallmentPlanCount: number;
  reviewInstallmentPlanCount: number;
  installmentPriceReviewRequired: boolean;
  lowestPeriodicPayment: number | null;
  lowestInstallmentTotal: number | null;
  lowestVisiblePeriodicPayment: number | null;
  lowestVisibleInstallmentTotal: number | null;
  purchaseLabel: string | null;
  imageUrl: string | null;
  priceHistory: ListingPriceHistory;
};

export type PricedProductsPage = {
  items: PricedProductSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type LiveStoreSummary = {
  storeId: string;
  name: string;
  baseUrl: string | null;
  primaryCategory: string | null;
  storeType: string | null;
  mappedProductCount: number;
  pricedProductCount: number;
  verifiedPricedProductCount: number;
  reviewPricedProductCount: number;
  installmentPlanCount: number;
  connected: boolean;
  priced: boolean;
  latestCashUpdate: string | null;
};

function finitePositive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function validPublicCashPrice(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 10;
}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function cleanCurrency(value: unknown) {
  return String(value ?? "EGP").trim().toUpperCase();
}

function validCashOffers(value: unknown): LiveCashOffer[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((offer, index) => {
    if (
      !offer ||
      typeof offer !== "object" ||
      !validPublicCashPrice(offer.cash_price) ||
      cleanCurrency(offer.currency) !== "EGP" ||
      !String(offer.store_name ?? "").trim()
    ) {
      return [];
    }
    return [
      {
        ...offer,
        offer_id: String(offer.offer_id ?? `offer-${index}`),
        store_name: String(offer.store_name).trim(),
        cash_price: offer.cash_price,
        currency: "EGP" as const,
      },
    ];
  });
}

function validInstallmentPlans(value: unknown): LiveInstallmentPlan[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap((plan, index) => {
    if (
      !plan ||
      typeof plan !== "object" ||
      !String(plan.store_name ?? "").trim() ||
      cleanCurrency(plan.currency) !== "EGP"
    ) {
      return [];
    }
    const hasUsefulTerm = finitePositive(plan.months) && (
      finitePositive(plan.periodic_payment) ||
      finitePositive(plan.normalized_total) ||
      finitePositive(plan.total_published) ||
      finitePositive(plan.total_calculated)
    );
    if (!hasUsefulTerm) return [];
    const dedupeKey = [
      String(plan.store_name).trim().toLocaleLowerCase("ar-EG"),
      String(plan.provider_name ?? "").trim().toLocaleLowerCase("ar-EG"),
      plan.months,
      plan.periodic_payment ?? "",
      plan.down_payment ?? 0,
      plan.normalized_total ?? plan.total_published ?? plan.total_calculated ?? "",
    ].join("|");
    if (seen.has(dedupeKey)) return [];
    seen.add(dedupeKey);
    return [
      {
        ...plan,
        plan_id: String(plan.plan_id ?? `plan-${index}`),
        store_name: String(plan.store_name).trim(),
        months: finitePositive(plan.months) ? plan.months : null,
        periodic_payment: finitePositive(plan.periodic_payment)
          ? plan.periodic_payment
          : null,
        down_payment: finiteNonNegative(plan.down_payment)
          ? plan.down_payment
          : null,
        admin_fees: finiteNonNegative(plan.admin_fees)
          ? plan.admin_fees
          : null,
        currency: "EGP" as const,
      },
    ];
  });
}

function parseSparkline(value: unknown): Array<{ date: string; price: number }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((point) => {
    if (!point || typeof point !== "object") return [];
    const row = point as Record<string, unknown>;
    const date = String(row.date ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !finitePositive(row.price)) return [];
    return [{ date, price: row.price }];
  });
}

function validPricedProducts(value: unknown): PricedProductSummary[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const item = entry as Record<string, unknown>;
    const variantId = String(item.variant_id ?? "");
    const canonicalName = String(item.canonical_name ?? "").trim();
    const brand = String(item.brand ?? "").trim();
    const model = String(item.model ?? "").trim();
    if (
      !/^VAR-[A-Z0-9]{6,40}$/.test(variantId) ||
      !canonicalName ||
      !brand ||
      !model
    ) {
      return [];
    }
    const history = item.price_history && typeof item.price_history === "object"
      ? item.price_history as Record<string, unknown>
      : {};
    return [
      {
        variantId,
        canonicalName,
        section: String(item.section ?? ""),
        productType: item.product_type != null ? String(item.product_type) : null,
        brand,
        model,
        variantName: item.variant_name != null ? String(item.variant_name) : null,
        lowestCashPrice: validPublicCashPrice(item.lowest_cash_price)
          ? item.lowest_cash_price
          : null,
        lowestConfirmedCashPrice: validPublicCashPrice(item.lowest_confirmed_cash_price)
          ? item.lowest_confirmed_cash_price
          : null,
        lowestDeliveredTotal: validPublicCashPrice(item.lowest_delivered_total)
          ? item.lowest_delivered_total
          : null,
        lowestFinalCost: validPublicCashPrice(item.lowest_final_cost)
          ? item.lowest_final_cost
          : null,
        cashOfferCount: finiteNonNegative(item.cash_offer_count)
          ? item.cash_offer_count
          : 0,
        confirmedCashOfferCount: finiteNonNegative(item.confirmed_cash_offer_count)
          ? item.confirmed_cash_offer_count
          : 0,
        reviewCashOfferCount: finiteNonNegative(item.review_cash_offer_count)
          ? item.review_cash_offer_count
          : 0,
        cashPriceReviewRequired: item.cash_price_review_required === true,
        installmentPlanCount: finiteNonNegative(item.installment_plan_count)
          ? item.installment_plan_count
          : 0,
        confirmedInstallmentPlanCount: finiteNonNegative(item.confirmed_installment_plan_count)
          ? item.confirmed_installment_plan_count
          : 0,
        reviewInstallmentPlanCount: finiteNonNegative(item.review_installment_plan_count)
          ? item.review_installment_plan_count
          : 0,
        installmentPriceReviewRequired: item.installment_price_review_required === true,
        lowestPeriodicPayment: finitePositive(item.lowest_periodic_payment)
          ? item.lowest_periodic_payment
          : null,
        lowestInstallmentTotal: finitePositive(item.lowest_installment_total)
          ? item.lowest_installment_total
          : null,
        lowestVisiblePeriodicPayment: finitePositive(item.lowest_visible_periodic_payment)
          ? item.lowest_visible_periodic_payment
          : null,
        lowestVisibleInstallmentTotal: finitePositive(item.lowest_visible_installment_total)
          ? item.lowest_visible_installment_total
          : null,
        purchaseLabel: item.purchase_label ? String(item.purchase_label) : null,
        imageUrl: typeof item.image_url === "string" && item.image_url.startsWith("https://")
          ? item.image_url
          : null,
        priceHistory: {
          lowest30d: finitePositive(history.lowest_30d) ? history.lowest_30d : null,
          lowest90d: finitePositive(history.lowest_90d) ? history.lowest_90d : null,
          average90d: finitePositive(history.average_90d) ? history.average_90d : null,
          highest90d: finitePositive(history.highest_90d) ? history.highest_90d : null,
          sparkline: parseSparkline(history.sparkline),
        },
      },
    ];
  });
}

async function apiHeaders(apiBase: string) {
  return {
    Accept: "application/json",
    ...(await cloudRunAuthorizationHeaders(apiBase)),
  };
}

export async function getLiveComparison(
  productId: string,
): Promise<LiveComparison> {
  const apiBase = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  if (!apiBase || !/^VAR-[A-Z0-9]{6,40}$/.test(productId)) {
    return { cashOffers: [], installmentPlans: [], productImageUrl: null };
  }
  try {
    const response = await fetch(
      `${apiBase}/api/v1/products/${encodeURIComponent(productId)}/comparison`,
      {
        headers: await apiHeaders(apiBase),
        cache: "no-store",
        signal: AbortSignal.timeout(8_000),
      },
    );
    if (!response.ok) {
      return { cashOffers: [], installmentPlans: [], productImageUrl: null };
    }
    const data = (await response.json()) as {
      cash_offers?: unknown;
      installment_plans?: unknown;
      product?: unknown;
    };
    return {
      cashOffers: validCashOffers(data.cash_offers),
      installmentPlans: validInstallmentPlans(data.installment_plans),
      productImageUrl:
        data.product && typeof data.product === "object"
        && typeof (data.product as Record<string, unknown>).image_url === "string"
        && String((data.product as Record<string, unknown>).image_url).startsWith("https://")
          ? String((data.product as Record<string, unknown>).image_url)
          : null,
    };
  } catch {
    return { cashOffers: [], installmentPlans: [], productImageUrl: null };
  }
}

export async function getPricedProducts(
  mode: "cash" | "installment",
  {
    limit,
    offset,
    query,
    section,
  }: { limit: number; offset: number; query?: string; section?: string },
): Promise<PricedProductsPage> {
  const apiBase = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  const empty: PricedProductsPage = { items: [], total: 0, limit, offset };
  if (!apiBase) return empty;
  try {
    const params = new URLSearchParams({
      mode,
      limit: String(limit),
      offset: String(offset),
    });
    if (query?.trim()) params.set("q", query.trim().slice(0, 160));
    if (section?.trim()) params.set("section", section.trim().slice(0, 120));
    const response = await fetch(
      `${apiBase}/api/v1/products/priced?${params}`,
      {
        headers: await apiHeaders(apiBase),
        cache: "no-store",
        signal: AbortSignal.timeout(8_000),
      },
    );
    if (!response.ok) return empty;
    const data = (await response.json()) as { items?: unknown; total?: unknown };
    const items = validPricedProducts(data.items).filter((item) =>
      mode === "cash"
        ? item.lowestFinalCost != null
          || item.lowestDeliveredTotal != null
          || item.lowestCashPrice != null
        : item.installmentPlanCount > 0 && (
          item.lowestInstallmentTotal != null
          || item.lowestVisibleInstallmentTotal != null
          || item.lowestPeriodicPayment != null
          || item.lowestVisiblePeriodicPayment != null
        )
    );
    return {
      items,
      total: finiteNonNegative(data.total) ? data.total : 0,
      limit,
      offset,
    };
  } catch {
    return empty;
  }
}

export async function getLiveStores(query?: string): Promise<LiveStoreSummary[]> {
  const apiBase = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  if (!apiBase) return [];
  const params = new URLSearchParams({ limit: "500" });
  if (query?.trim()) params.set("q", query.trim().slice(0, 120));
  try {
    const response = await fetch(`${apiBase}/api/v1/stores?${params}`, {
      headers: await apiHeaders(apiBase),
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return [];
    const body = (await response.json()) as { items?: unknown };
    if (!Array.isArray(body.items)) return [];
    return body.items.flatMap((raw) => {
      if (!raw || typeof raw !== "object") return [];
      const item = raw as Record<string, unknown>;
      const storeId = String(item.store_id ?? "").trim();
      const name = String(item.name ?? "").trim();
      if (!storeId || !name) return [];
      return [{
        storeId,
        name,
        baseUrl: item.base_url ? String(item.base_url) : null,
        primaryCategory: item.primary_category ? String(item.primary_category) : null,
        storeType: item.store_type ? String(item.store_type) : null,
        mappedProductCount: finiteNonNegative(item.mapped_product_count) ? item.mapped_product_count : 0,
        pricedProductCount: finiteNonNegative(item.priced_product_count) ? item.priced_product_count : 0,
        verifiedPricedProductCount: finiteNonNegative(item.verified_priced_product_count)
          ? item.verified_priced_product_count
          : 0,
        reviewPricedProductCount: finiteNonNegative(item.review_priced_product_count)
          ? item.review_priced_product_count
          : 0,
        installmentPlanCount: finiteNonNegative(item.installment_plan_count) ? item.installment_plan_count : 0,
        connected: item.connected === true,
        priced: item.priced === true,
        latestCashUpdate: item.latest_cash_update ? String(item.latest_cash_update) : null,
      }];
    });
  } catch {
    return [];
  }
}

export function installmentTotal(plan: LiveInstallmentPlan) {
  return (
    plan.normalized_total ??
    plan.total_published ??
    plan.total_calculated ??
    null
  );
}
