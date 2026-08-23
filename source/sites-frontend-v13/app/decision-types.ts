export type DecisionMode = "cheapest" | "safest" | "fastest" | "installment";

export type PriceHistory = {
  lowest_30d: number | null;
  lowest_90d: number | null;
  average_90d: number | null;
  highest_90d: number | null;
  change_count: number;
  last_drop_at: string | null;
  trend: "stable" | "declining" | "rising" | "volatile" | "insufficient_data";
  volatility?: number;
  sparkline: Array<{ date: string; price: number }>;
  markers: Array<{ date: string; type: string; label: string; price: number }>;
};

export type DecisionCashOffer = {
  offer_id: string;
  store_id: string;
  store_name: string;
  seller_name?: string | null;
  currency?: string | null;
  cash_price?: number | null;
  shipping_cost?: number | null;
  shipping_cost_known?: boolean;
  mandatory_fees?: number | null;
  card_fees?: number | null;
  coupon_code?: string | null;
  coupon_discount?: number | null;
  final_cost?: number | null;
  availability?: string | null;
  pickup_available?: boolean | null;
  pickup_text?: string | null;
  min_delivery_days?: number | null;
  max_delivery_days?: number | null;
  warranty_type?: string | null;
  warranty_provider?: string | null;
  warranty_months?: number | null;
  source_url?: string | null;
  last_success_at?: string | null;
  decision_score?: number | null;
  safety_score?: number | null;
  delivery_score?: number | null;
  price_accuracy_score?: number | null;
  update_regularity_score?: number | null;
  availability_clarity_score?: number | null;
  warranty_clarity_score?: number | null;
  correct_destination_score?: number | null;
  broken_link_rate?: number | null;
  complaint_response_score?: number | null;
  store_quality_sample_size?: number | null;
  score_components?: Record<string, number>;
  anomaly_status?: "clear" | "review" | "blocked";
  anomaly_reasons?: string[];
  explanation?: string;
  price_position?: {
    label: string;
    tone: string;
    percent_vs_average: number | null;
  };
  match_evidence?: {
    mapping_id?: string | null;
    url?: string | null;
    store_sku?: string | null;
    manufacturer_sku?: string | null;
    title_as_seen?: string | null;
    match_confidence?: string | null;
    fields?: string[];
  };
};

export type DecisionInstallmentPlan = {
  plan_id: string;
  store_id: string;
  store_name: string;
  provider_name?: string | null;
  bank_or_card?: string | null;
  months?: number | null;
  periodic_payment?: number | null;
  down_payment?: number | null;
  admin_fees?: number | null;
  processing_fees?: number | null;
  insurance_fees?: number | null;
  other_fees?: number | null;
  card_fees?: number | null;
  shipping_cost?: number | null;
  coupon_discount?: number | null;
  total_published?: number | null;
  total_calculated?: number | null;
  final_installment_cost?: number | null;
  interest_free?: boolean | null;
  source_url?: string | null;
  last_success_at?: string | null;
  lowest_total?: boolean;
  low_payment_high_total?: boolean;
  explanation?: string;
};

export type SmartAlternative = {
  variant_id: string;
  canonical_name: string;
  brand?: string | null;
  model?: string | null;
  variant_name?: string | null;
  lowest_final_cost?: number | null;
  lowest_installment_total?: number | null;
  similarity_score: number;
  price_gap?: number | null;
  alternative_type: string;
  reason: string;
};

export type DecisionComparison = {
  product: Record<string, unknown> & {
    variant_id?: string;
    id?: string;
    canonical_name?: string;
    name?: string;
    model?: string;
    brand?: string;
  };
  purchase_index: {
    label: string;
    tone: string;
    percent_vs_average: number | null;
    score?: number | null;
    explanation?: string;
    best_offer_id?: string | null;
  };
  history: PriceHistory;
  cash_offers: DecisionCashOffer[];
  installment_plans: DecisionInstallmentPlan[];
  mode_orders: Record<DecisionMode, string[]>;
  mode_labels: Record<DecisionMode, string>;
  alternatives: SmartAlternative[];
  known_store_count: number;
  last_known_cash_price?: number | null;
  last_known_cash_price_at?: string | null;
  degraded_components?: string[];
};

export type ProductComparisonMatrix = {
  products: Array<Record<string, unknown> & {
    variant_id: string;
    canonical_name: string;
    brand?: string | null;
    model?: string | null;
    variant_name?: string | null;
    lowest_final_cost?: number | null;
    lowest_installment_total?: number | null;
    warranty_months?: number | null;
    confirmed_store_count?: number;
    value_score?: number;
    specs_normalized?: Record<string, string>;
  }>;
  matrix: Array<{ name: string; values: Record<string, string | null | undefined> }>;
  best_value_variant_id: string | null;
  explanation?: string | null;
};
