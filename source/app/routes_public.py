from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request

from app.decision_engine_runtime import (
    compare_products,
    create_alert_rule,
    create_comparison_share,
    get_purchase_decision,
    report_price_issue,
    smart_search,
)
from app.priced_products import list_priced_products
from app.public_stores import list_public_stores
from app.repository_provider import repository
from app.store_quality import get_store_quality

router = APIRouter(prefix="/api/v1", tags=["public"])
JsonBody = Annotated[dict, Body()]


@router.get("/catalog/sections")
def catalog_sections():
    return {"items": repository.catalog_sections()}


@router.get("/catalog/brands")
def catalog_brands(section: str | None = Query(default=None, max_length=120)):
    return {"section": section, "items": repository.catalog_brands(section)}


@router.get("/catalog/product-types")
def catalog_product_types(section: str | None = Query(default=None, max_length=120)):
    return {"section": section, "items": repository.catalog_product_types(section)}


@router.get("/catalog/models")
def catalog_models(
    section: str | None = Query(default=None, max_length=120),
    brand: str | None = Query(default=None, max_length=120),
):
    return {"section": section, "brand": brand, "items": repository.catalog_models(section, brand)}


@router.get("/catalog/variants")
def catalog_variants(
    section: str | None = Query(default=None, max_length=120),
    brand: str | None = Query(default=None, max_length=120),
    model: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return {
        "section": section,
        "brand": brand,
        "model": model,
        "items": repository.catalog_variants(
            section=section, brand=brand, model=model, limit=limit
        ),
    }


@router.get("/products/search/smart")
def smart_product_search(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
):
    return smart_search(q, limit=limit)


@router.get("/products/search")
def search_products(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    section: str | None = Query(default=None, max_length=120),
    brand: str | None = Query(default=None, max_length=120),
):
    return {
        "query": q,
        "items": repository.search_products(q, limit=limit, section=section, brand=brand),
    }


@router.get("/products/priced")
def priced_products(
    mode: Literal["cash", "installment"] = Query(default="cash"),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=160),
    section: str | None = Query(default=None, max_length=120),
):
    return list_priced_products(
        mode=mode,
        limit=limit,
        offset=offset,
        query=q,
        section=section,
    )


@router.post("/products/compare")
def product_matrix(payload: JsonBody):
    variant_ids = payload.get("variant_ids")
    if not isinstance(variant_ids, list):
        raise HTTPException(status_code=422, detail="variant_ids must be a list")
    try:
        return compare_products([str(item) for item in variant_ids])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/comparisons/share")
def share_product_matrix(payload: JsonBody):
    variant_ids = payload.get("variant_ids")
    if not isinstance(variant_ids, list):
        raise HTTPException(status_code=422, detail="variant_ids must be a list")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    try:
        return create_comparison_share([str(item) for item in variant_ids], settings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/alerts")
def create_price_alert(payload: JsonBody):
    try:
        return create_alert_rule(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reports")
def create_price_report(request: Request, payload: JsonBody):
    client = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")[:300]
    fingerprint = hashlib.sha256(f"{client}|{user_agent}".encode()).hexdigest()
    try:
        return report_price_issue(payload, reporter_fingerprint=fingerprint)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stores/{store_id}/quality")
def store_quality(
    store_id: str = Path(max_length=80),
    recalculate: bool = Query(default=True),
):
    data = get_store_quality(store_id, recalculate=recalculate)
    if data is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return data


@router.get("/products/{variant_id}/decision")
def product_purchase_decision(variant_id: str = Path(max_length=80)):
    data = get_purchase_decision(variant_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Product variant not found")
    return data


@router.get("/products/{variant_id}/comparison")
def product_comparison(
    variant_id: str = Path(max_length=80),
    include_unpriced: bool = Query(default=False),
):
    data = repository.get_product_comparison(variant_id, include_unpriced=include_unpriced)
    if data is None:
        raise HTTPException(status_code=404, detail="Product variant not found")
    return data


@router.get("/products/{variant_id}/cash")
def product_cash_offers(
    variant_id: str = Path(max_length=80),
    include_unpriced: bool = Query(default=False),
):
    data = repository.get_product_comparison(variant_id, include_unpriced=include_unpriced)
    if data is None:
        raise HTTPException(status_code=404, detail="Product variant not found")
    return {"product": data["product"], "cash_offers": data["cash_offers"]}


@router.get("/products/{variant_id}/installments")
def product_installment_plans(variant_id: str = Path(max_length=80)):
    data = repository.get_product_comparison(variant_id, include_unpriced=False)
    if data is None:
        raise HTTPException(status_code=404, detail="Product variant not found")
    return {"product": data["product"], "installment_plans": data["installment_plans"]}


@router.get("/status")
def public_status():
    return repository.system_stats()


@router.get("/stores")
def public_stores(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=500, ge=1, le=500),
):
    return list_public_stores(query=q, limit=limit)
