from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MappingTarget(BaseModel):
    mapping_id: str
    offer_id: str
    offer_key: str
    variant_id: str
    store_id: str
    store_name: str | None = None
    store_base_url: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    store_sku: str | None = None
    source_url: str
    url_type: str | None = None
    title_as_seen: str | None = None
    match_method: str | None = None
    match_confidence: str | None = None
    extraction_hint: str | None = None

    canonical_name: str
    section: str | None = None
    product_type: str | None = None
    brand: str | None = None
    model: str | None = None
    variant_name: str | None = None
    ram_gb: float | None = None
    storage_gb: float | None = None
    color: str | None = None
    manufacturer_sku: str | None = None
    gtin: str | None = None


class ScrapeGroupPayload(BaseModel):
    task_id: str
    run_id: str
    run_slot: datetime
    scheduled_for: datetime
    store_id: str
    store_name: str
    source_url: str
    url_type: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    connector_mode: str = "auto"
    connector_version: str = "generic-v1"
    connector_config: dict[str, Any] = Field(default_factory=dict)
    requests_per_minute: int = 10
    max_concurrency: int = 1
    respect_robots: bool = True
    browser_required: bool = False
    mapping_ids: list[str] = Field(default_factory=list)
    mappings: list[MappingTarget] = Field(default_factory=list)


class ScheduleRequest(BaseModel):
    trigger: str = "manual"


class CatalogScheduleRequest(BaseModel):
    trigger: str = "manual"
    store_limit: int | None = Field(default=None, ge=1, le=500)


class CatalogDiscoveryTaskPayload(BaseModel):
    task_id: str
    run_id: str
    run_slot: datetime
    scheduled_for: datetime
    source_id: str
    store_id: str
    store_name: str
    source_url: str
    source_type: str = "auto"
    allowed_hosts: list[str] = Field(default_factory=list)
    connector_version: str = "catalog-generic-v1"
    connector_config: dict[str, Any] = Field(default_factory=dict)
    requests_per_minute: int = 6
    max_concurrency: int = 1
    respect_robots: bool = True
    browser_required: bool = False
    delivery_generation: int = Field(default=1, ge=1, le=100)


class CatalogRecoveryRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


class CatalogBootstrapImportRequest(BaseModel):
    provider: str = Field(
        default="ultimate_web_scraper",
        pattern=r"^[a-z0-9][a-z0-9_.-]{1,79}$",
    )
    external_run_id: str = Field(min_length=2, max_length=200)
    store_id: str = Field(min_length=2, max_length=80)
    records: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CashOfferExtract(BaseModel):
    currency: str = "EGP"
    cash_price: float
    old_price: float | None = None
    shipping_cost: float | None = None
    free_shipping: bool | None = None
    availability: str | None = None
    available_quantity: float | None = None
    delivery_text: str | None = None
    min_delivery_days: float | None = None
    max_delivery_days: float | None = None
    warranty_type: str | None = None
    warranty_provider: str | None = None
    warranty_months: float | None = None
    seller_name: str | None = None
    source_url: str
    source_method: str
    product_title: str | None = None
    image_url: str | None = None
    match_score: float = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class InstallmentPlanExtract(BaseModel):
    provider_name: str | None = None
    provider_type: str | None = None
    bank_or_card: str | None = None
    plan_name: str | None = None
    months: int | None = None
    payment_frequency: str = "monthly"
    periodic_payment: float | None = None
    first_payment: float | None = None
    down_payment: float | None = None
    down_payment_percent: float | None = None
    admin_fees: float | None = None
    processing_fees: float | None = None
    insurance_fees: float | None = None
    other_fees: float | None = None
    total_published: float | None = None
    total_calculated: float | None = None
    cash_price_at_observation: float | None = None
    financing_cost: float | None = None
    financing_markup_percent: float | None = None
    apr: float | None = None
    interest_type: str | None = None
    interest_free: bool | None = None
    grace_months: int | None = None
    minimum_purchase: float | None = None
    maximum_financing: float | None = None
    eligibility: str | None = None
    required_card: str | None = None
    customer_type: str | None = None
    new_customers_only: bool | None = None
    geography: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    promo_code: str | None = None
    terms_url: str | None = None
    source_url: str
    starting_from_only: bool = False
    completeness: str = "partial"
    raw: dict[str, Any] = Field(default_factory=dict)


class FetchResult(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str | None = None
    body: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    response_bytes: int = 0
    not_modified: bool = False
    used_browser: bool = False


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(resolved|rejected|ignored)$")
    resolution: str = Field(min_length=2, max_length=2000)
    actor: str = Field(min_length=3, max_length=320)
