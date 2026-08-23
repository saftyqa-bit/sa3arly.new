import { cloudRunAuthorizationHeaders } from "../cloud-run-auth";

export type AdminSummary = {
  summary: Partial<{
    products: number;
    variants: number;
    active_stores: number;
    active_mappings: number;
    cash_offers: number;
    installment_plans: number;
    open_reviews: number;
    urgent_reviews: number;
    catalog_needs_review: number;
    provisional_variants: number;
    weak_mappings: number;
    latest_cash_update: string | null;
    latest_installment_update: string | null;
  }>;
  reviews: Array<{ severity: string; status: string; count: number }>;
  recent_runs: Array<{
    run_id: string;
    trigger_source: string;
    status: string;
    mapping_count: number;
    queued_task_count: number;
    completed_task_count: number;
    failed_task_count: number;
    started_at?: string | null;
    completed_at?: string | null;
  }>;
  stores_needing_attention: Array<{
    store_id: string;
    name: string;
    priority?: string | null;
    mappings: number;
    latest_price_update?: string | null;
    connector_failures: number;
    open_reviews: number;
  }>;
  preview?: boolean;
};

export type ReviewQueue = {
  items: Array<{
    review_id: string;
    entity_type: string;
    entity_id: string;
    issue_code: string;
    severity: string;
    status: string;
    title: string;
    description?: string | null;
    payload?: Record<string, unknown>;
    assigned_to?: string | null;
    resolution?: string | null;
    created_at?: string | null;
  }>;
  pagination: {
    limit: number;
    offset: number;
    returned: number;
    total: number;
    has_more: boolean;
  };
};

export type AdminProducts = {
  items: Array<{
    product_id: string;
    product_name: string;
    model?: string | null;
    source_status: string;
    brand_name?: string | null;
    category_name?: string | null;
    parent_category_name?: string | null;
    variant_count: number;
    connected_store_count: number;
    lowest_cash_price?: number | null;
    cash_offer_count: number;
    installment_plan_count: number;
  }>;
  pagination: {
    limit: number;
    offset: number;
    returned: number;
    total: number;
    has_more: boolean;
  };
};

function apiConfiguration() {
  const baseUrl = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  const internalToken = process.env.SA3ARLY_INTERNAL_TOKEN;
  return baseUrl && internalToken ? { baseUrl, internalToken } : null;
}

async function internalGet<T>(path: string): Promise<T> {
  const config = apiConfiguration();
  if (!config) throw new Error("Admin API is not configured");
  const response = await fetch(`${config.baseUrl}${path}`, {
    headers: {
      Accept: "application/json",
      "X-Internal-Token": config.internalToken,
      ...(await cloudRunAuthorizationHeaders(config.baseUrl)),
    },
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    throw new Error(`Admin API returned ${response.status}`);
  }
  return (await response.json()) as T;
}

function previewSummary(): AdminSummary {
  return {
    preview: true,
    summary: {
      products: 2471,
      variants: 2471,
      active_stores: 209,
      active_mappings: 2332,
      cash_offers: 14,
      installment_plans: 0,
      open_reviews: 0,
      urgent_reviews: 0,
      weak_mappings: 0,
      latest_cash_update: "2026-07-31T10:00:17.354432+00:00",
      latest_installment_update: null,
    },
    reviews: [],
    recent_runs: [
      {
        run_id: "preview-run",
        trigger_source: "scheduler",
        status: "ready-for-migration",
        mapping_count: 2332,
        queued_task_count: 1136,
        completed_task_count: 0,
        failed_task_count: 0,
      },
    ],
    stores_needing_attention: [
      {
        store_id: "preview-store-1",
        name: "متاجر لم تبدأ الأسعار بعد",
        priority: "high",
        mappings: 2318,
        connector_failures: 0,
        open_reviews: 0,
      },
      {
        store_id: "preview-store-2",
        name: "متاجر بأسعار مؤكدة",
        priority: "live",
        mappings: 14,
        connector_failures: 0,
        open_reviews: 0,
        latest_price_update: "2026-07-31T10:00:17.354432+00:00",
      },
    ],
  };
}

function previewReviews(): ReviewQueue {
  return {
    items: [
      {
        review_id: "00000000-0000-0000-0000-000000000001",
        entity_type: "store_product_mapping",
        entity_id: "MAP-PREVIEW",
        issue_code: "mapping_review_required",
        severity: "high",
        status: "open",
        title: "مراجعة ربط متجر بمنتج",
        description:
          "تظهر هنا المطابقات الغامضة بعد تطبيق Migration 005، ويمكن اعتمادها أو رفضها من نفس اللوحة.",
        created_at: new Date().toISOString(),
      },
    ],
    pagination: { limit: 20, offset: 0, returned: 1, total: 1, has_more: false },
  };
}

function previewProducts(): AdminProducts {
  return {
    items: [
      {
        product_id: "PRD-PREVIEW-IPHONE",
        product_name: "Apple iPhone 16",
        model: "iPhone 16",
        source_status: "mapped",
        brand_name: "Apple",
        category_name: "الهواتف المحمولة",
        parent_category_name: "الإلكترونيات",
        variant_count: 4,
        connected_store_count: 3,
        lowest_cash_price: 49999,
        cash_offer_count: 3,
        installment_plan_count: 0,
      },
      {
        product_id: "PRD-PREVIEW-HEADPHONE",
        product_name: "Anker Soundcore Q11i",
        model: "Q11i",
        source_status: "catalog_verified",
        brand_name: "Anker",
        category_name: "سماعات الرأس",
        parent_category_name: "الإلكترونيات",
        variant_count: 2,
        connected_store_count: 1,
        lowest_cash_price: 1299,
        cash_offer_count: 1,
        installment_plan_count: 0,
      },
    ],
    pagination: { limit: 30, offset: 0, returned: 2, total: 2, has_more: false },
  };
}

export async function loadAdminDashboard() {
  const allowPreview =
    process.env.NODE_ENV !== "production" &&
    process.env.SA3ARLY_ADMIN_PREVIEW === "1";
  try {
    const [summary, reviews, products] = await Promise.all([
      internalGet<AdminSummary>("/internal/admin/summary"),
      internalGet<ReviewQueue>("/internal/admin/review-queue?status=open&limit=20"),
      internalGet<AdminProducts>("/internal/admin/products?limit=30"),
    ]);
    return { summary, reviews, products, error: null };
  } catch (error) {
    if (allowPreview) {
      return {
        summary: previewSummary(),
        reviews: previewReviews(),
        products: previewProducts(),
        error: null,
      };
    }
    return {
      summary: null,
      reviews: null,
      products: null,
      error: error instanceof Error ? error.message : "تعذر تحميل لوحة الإدارة",
    };
  }
}
