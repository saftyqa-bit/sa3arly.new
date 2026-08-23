from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_mode: Literal["api", "worker", "all"] = "all"
    port: int = 8080

    persistence_backend: Literal["postgres", "firestore"] = "postgres"
    database_url: str = "postgresql://sa3arly:sa3arly@localhost:5432/sa3arly"
    db_pool_min: int = 1
    db_pool_max: int = 10

    scheduler_timezone: str = "Africa/Cairo"
    refresh_cron: str = "0 10,20 * * *"
    refresh_interval_minutes: int = 720
    task_stagger_seconds: int = 2700
    freshness_minutes: int = 855
    stale_after_minutes: int = 1710
    max_mappings_per_task: int = 50
    max_url_groups_per_run: int = 10_000
    inline_max_url_groups: int = 25
    stale_task_after_minutes: int = 180
    price_run_finalizer_cadence_minutes: int = 5

    # Catalog discovery is deliberately independent from the twice-daily
    # price refresh.  A small rotating store batch prevents catalog work from
    # delaying the 10:00/20:00 Cairo price runs.
    catalog_discovery_cron: str = "30 2 * * *"
    catalog_discovery_stores_per_run: int = 35
    # A full run must never be tied to the current registry size.  Keep a
    # generous safety ceiling so adding stores cannot silently truncate the
    # next full discovery cycle.
    catalog_discovery_full_store_limit: int = 500
    catalog_discovery_rescan_hours: int = 168
    catalog_discovery_max_candidates_per_store: int = 2_000
    catalog_discovery_max_sitemaps_per_store: int = 8
    catalog_discovery_max_source_fetches: int = 12
    catalog_discovery_max_product_fetches_per_store: int = 25
    catalog_discovery_auto_match_score: float = 95.0
    catalog_discovery_review_score: float = 70.0
    catalog_discovery_new_product_min_stores: int = 2

    firestore_project_id: str = ""
    firestore_database: str = "(default)"
    firestore_collection_prefix: str = ""
    firestore_catalog_cache_seconds: int = 300
    firestore_page_cache_max_bytes: int = 700_000
    firestore_comparison_max_bytes: int = 900_000
    firestore_lock_wait_seconds: int = 900
    firestore_task_retention_days: int = 14
    firestore_run_retention_days: int = 90
    firestore_page_cache_retention_days: int = 7

    tasks_mode: Literal["inline", "cloud"] = "inline"
    gcp_project_id: str = ""
    gcp_region: str = "europe-west1"
    cloud_tasks_location: str = "europe-west1"
    cloud_tasks_queue: str = "sa3arly-scrape"
    catalog_tasks_queue: str = "sa3arly-catalog-discovery"
    cloud_tasks_max_attempts: int = 3
    task_dispatch_deadline_seconds: int = 900
    worker_url: str = "http://localhost:8081"
    tasks_service_account_email: str = ""
    internal_token: str = "change-this-local-token"

    user_agent: str = "Sa3arlyPriceBot/1.0"
    http_timeout_seconds: float = 25.0
    max_response_bytes: int = 6_000_000
    max_redirects: int = 5
    max_retry_after_seconds: int = 3600
    max_store_request_wait_seconds: float = 15.0
    store_rate_limit_cooldown_seconds: int = 300
    store_transient_cooldown_seconds: int = 60
    respect_robots_txt: bool = True
    enable_browser_fallback: bool = True
    follow_discovered_product_urls: bool = True
    max_discovered_fetches_per_task: int = 10
    min_direct_match_score: float = 40.0
    min_listing_match_score: float = 55.0
    default_currency: str = "EGP"
    default_delivery_region: str = "Cairo"
    min_price_ratio_to_previous: float = 0.20
    max_price_ratio_to_previous: float = 5.00

    allowed_origins: str = "http://localhost:3000,http://localhost:8080"
    log_level: str = "INFO"

    @property
    def allowed_origin_list(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]

    @property
    def worker_task_url(self) -> str:
        return self.worker_url.rstrip("/") + "/internal/tasks/scrape"

    @property
    def scheduler_url(self) -> str:
        return self.worker_url.rstrip("/") + "/internal/scheduler/refresh"

    @property
    def catalog_worker_task_url(self) -> str:
        return self.worker_url.rstrip("/") + "/internal/tasks/catalog-discovery"

    @property
    def catalog_scheduler_url(self) -> str:
        return self.worker_url.rstrip("/") + "/internal/scheduler/catalog-discovery"

    @property
    def price_run_finalization_deadline_minutes(self) -> int:
        """Trigger early enough for the next check to stay within half-time."""

        half_interval = max(1, self.refresh_interval_minutes // 2)
        return max(1, half_interval - self.price_run_finalizer_cadence_minutes)

    @property
    def price_run_scheduling_window_seconds(self) -> int:
        """Leave a bounded drain buffer before the price-run finalizer."""

        deadline_seconds = self.price_run_finalization_deadline_minutes * 60
        drain_buffer_seconds = min(300, max(30, deadline_seconds // 10))
        return max(1, deadline_seconds - drain_buffer_seconds)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
