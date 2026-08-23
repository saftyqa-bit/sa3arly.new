from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl


TITLE_ROWS = 3


def read_sheet(workbook: Any, name: str) -> list[dict[str, Any]]:
    worksheet = workbook[name]
    rows = worksheet.iter_rows(values_only=True)
    for _ in range(TITLE_ROWS - 1):
        next(rows)
    headers = [str(value) if value is not None else "" for value in next(rows)]
    records: list[dict[str, Any]] = []
    for row in rows:
        if not any(value is not None for value in row):
            continue
        records.append(dict(zip(headers, row, strict=False)))
    return records


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def first_url(value: Any) -> str:
    return text(value).split(" | ", 1)[0].strip()


def split_ordered_specs(value: Any) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for part in text(value).split(" | "):
        if not part or ": " not in part:
            continue
        name, spec_value = part.split(": ", 1)
        if name.strip() and spec_value.strip():
            specs.append({"name": name.strip(), "value": spec_value.strip()})
    return specs


def inferred_specs(row: dict[str, Any]) -> dict[str, str]:
    searchable = " | ".join(
        [
            text(row.get("اسم النسخة الأصلي")),
            text(row.get("الاسم الموحّد")),
            text(row.get("الموديل")),
        ]
    )
    inferred: dict[str, str] = {}
    patterns = {
        "الحمولة بالكيلوجرام": r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:kg|kgs|كجم|كيلو)",
        "السعة باللتر": r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:l|liters?|litres?|لتر)(?![a-z])",
        "مقاس الشاشة": r"(?<!\d)(\d{2,3}(?:\.\d+)?)\s*(?:inch(?:es)?|in\b|بوصة|\")",
        "سرعة العصر": r"(?<!\d)(\d{3,4})\s*(?:rpm|لفة)",
    }
    units = {
        "الحمولة بالكيلوجرام": " كجم",
        "السعة باللتر": " لتر",
        "مقاس الشاشة": " بوصة",
        "سرعة العصر": " لفة/دقيقة",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, searchable, flags=re.IGNORECASE)
        if match:
            inferred[name] = f"{match.group(1)}{units[name]}"

    loading_patterns = [
        (r"\bfront[\s-]*load(?:ing)?\b|تحميل أمامي", "تحميل أمامي"),
        (r"\btop[\s-]*load(?:ing)?\b|تحميل علوي", "تحميل علوي"),
    ]
    for pattern, label in loading_patterns:
        if re.search(pattern, searchable, flags=re.IGNORECASE):
            inferred["نوع التحميل"] = label
            break

    resolution_match = re.search(
        r"\b(8K|4K|UHD|QHD|FHD|Full HD|HD)\b",
        searchable,
        flags=re.IGNORECASE,
    )
    if resolution_match:
        inferred["الدقة"] = resolution_match.group(1).upper()

    technology_match = re.search(
        r"\b(OLED|QLED|MINI[\s-]?LED|NANOCELL|LED)\b",
        searchable,
        flags=re.IGNORECASE,
    )
    if technology_match:
        inferred["تقنية الشاشة"] = technology_match.group(1).upper()

    return inferred


def compact_specs(
    row: dict[str, Any], priority_names: list[str]
) -> list[dict[str, str]]:
    original_specs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    candidates = [
        {
            "name": text(row.get("اسم المواصفة 1")),
            "value": text(row.get("قيمة المواصفة 1")),
        },
        {
            "name": text(row.get("اسم المواصفة 2")),
            "value": text(row.get("قيمة المواصفة 2")),
        },
        *split_ordered_specs(row.get("بقية المواصفات بالترتيب")),
    ]
    for candidate in candidates:
        pair = (candidate["name"], candidate["value"])
        if not all(pair) or pair in seen:
            continue
        seen.add(pair)
        original_specs.append(candidate)

    values_by_name = {
        specification["name"]: specification["value"]
        for specification in original_specs
    }
    for name, value in inferred_specs(row).items():
        values_by_name.setdefault(name, value)

    ordered: list[dict[str, str]] = []
    used_names: set[str] = set()
    for name in priority_names:
        value = values_by_name.get(name, "")
        if value and name not in used_names:
            ordered.append({"name": name, "value": value})
            used_names.add(name)
    for specification in original_specs:
        if (
            specification["name"] not in used_names
            and specification["name"] != "اسم النسخة"
        ):
            ordered.append(specification)
            used_names.add(specification["name"])
    if values_by_name.get("اسم النسخة"):
        ordered.append(
            {"name": "اسم النسخة", "value": values_by_name["اسم النسخة"]}
        )
    return ordered


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit(
            "Usage: import-selected-sectors-workbook.py INPUT.xlsx [OUTPUT.json]"
        )

    source = Path(sys.argv[1]).resolve()
    output = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) == 3
        else Path(__file__).resolve().parents[1] / "app" / "catalog-data.json"
    )
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)

    product_rows = read_sheet(workbook, "فهرس المقارنة")
    mapping_rows = read_sheet(workbook, "ربط المنتج بالمصدر")
    cash_rows = read_sheet(workbook, "جدول الكاش للموقع")
    installment_rows = read_sheet(workbook, "جدول التقسيط للموقع")
    source_rows = read_sheet(workbook, "دليل المصادر")
    launch_source_rows = read_sheet(workbook, "مصادر القطاعات المختارة")
    launch_type_rows = read_sheet(workbook, "أنواع القطاعات المختارة")
    filter_rows = read_sheet(workbook, "تكوين الفلاتر")

    priority_names_by_type_id = {
        text(row.get("معرّف النوع")): [
            name
            for name in (
                text(row.get(f"المواصفة {position}"))
                for position in range(1, 7)
            )
            if name
        ]
        for row in filter_rows
        if text(row.get("معرّف النوع"))
    }

    cash_by_key = {
        (text(row.get("معرّف النسخة")), text(row.get("معرّف المصدر"))): row
        for row in cash_rows
    }
    installment_by_key = {
        (text(row.get("معرّف النسخة")), text(row.get("معرّف المصدر"))): row
        for row in installment_rows
    }
    source_by_id = {
        text(row.get("المعرّف")): row
        for row in source_rows
        if text(row.get("المعرّف"))
    }

    sectors_by_source: dict[str, set[str]] = defaultdict(set)
    for row in launch_source_rows:
        sectors_by_source[text(row.get("معرّف المصدر"))].add(
            text(row.get("القطاع المختار"))
        )

    presence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mapped_source_ids: set[str] = set()
    for row in mapping_rows:
        variant_id = text(row.get("معرّف النسخة"))
        source_id = text(row.get("معرّف المصدر"))
        key = (variant_id, source_id)
        cash = cash_by_key.get(key, {})
        installment = installment_by_key.get(key, {})
        source_record = source_by_id.get(source_id, {})
        mapped_source_ids.add(source_id)
        presence[variant_id].append(
            {
                "mappingId": text(row.get("معرّف الربط")),
                "storeId": source_id,
                "storeName": text(row.get("اسم المصدر")),
                "entityType": text(source_record.get("نوع الكيان")),
                "presenceStatus": text(row.get("حالة وجود المنتج")),
                "sourceUrl": first_url(row.get("رابط المنتج/الدليل")),
                "linkType": text(row.get("نوع الرابط")),
                "matchConfidence": text(row.get("ثقة المطابقة")),
                "reviewStatus": text(row.get("حالة المراجعة")),
                "cashStatus": text(cash.get("حالة السعر")),
                "cashLabel": text(cash.get("النص الظاهر")),
                "installmentStatus": text(installment.get("اكتمال البيانات")),
                "installmentLabel": text(installment.get("النص الظاهر")),
            }
        )

    type_counts = Counter(text(row.get("نوع المنتج")) for row in product_rows)
    detailed_type_counts = Counter(
        text(row.get("النوع التفصيلي")) for row in product_rows
    )
    section_counts = Counter(text(row.get("القسم")) for row in product_rows)
    section_by_type: dict[str, str] = {}
    products: list[dict[str, Any]] = []
    for row in product_rows:
        product_type = text(row.get("نوع المنتج"))
        section = text(row.get("القسم"))
        section_by_type.setdefault(product_type, section)
        variant_id = text(row.get("معرّف النسخة"))
        specs = compact_specs(
            row,
            priority_names_by_type_id.get(
                text(row.get("معرّف نوع المنتج")), []
            ),
        )
        products.append(
            {
                "id": variant_id,
                "name": text(row.get("الاسم الموحّد")),
                "section": section,
                "category": text(row.get("الفئة النظامية")),
                "type": product_type,
                "subtype": text(row.get("التصنيف الفرعي")),
                "detailedType": text(row.get("النوع التفصيلي")),
                "brand": text(row.get("الماركة")),
                "model": text(row.get("الموديل")),
                "variant": text(row.get("اسم النسخة الأصلي")),
                "specs": specs,
                "sourceStatus": text(row.get("حالة المصدر")),
                "mappedStores": len(
                    {item["storeId"] for item in presence.get(variant_id, [])}
                ),
                "mappingRows": len(presence.get(variant_id, [])),
            }
        )

    registry_sources: list[dict[str, Any]] = []
    for row in source_rows:
        source_id = text(row.get("المعرّف"))
        registry_sources.append(
            {
                "id": source_id,
                "name": text(row.get("اسم المصدر")),
                "domain": text(row.get("النطاق")),
                "url": text(row.get("الرابط")),
                "entityType": text(row.get("نوع الكيان")),
                "priceStatus": text(row.get("حالة السعر")),
                "userLabel": text(row.get("العرض للمستخدم")),
                "projectScope": text(row.get("نطاق المشروع")),
                "selectedSectors": sorted(
                    sector
                    for sector in sectors_by_source.get(source_id, set())
                    if sector
                ),
            }
        )

    payload = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "dataVersion": "EG-8S-2026-07-31-v3",
        "sourceWorkbook": source.name,
        "stats": {
            "products": len(products),
            "registryStores": len(source_rows),
            "selectedSectorStores": len(sectors_by_source),
            "selectedSectorRelations": len(launch_source_rows),
            "connectedStores": len(mapped_source_ids),
            "mappings": len(mapping_rows),
            "sections": len(section_counts),
            "interfaceProductTypes": len(type_counts),
            "readyProductTypes": len(detailed_type_counts),
            "selectedProductTypes": sum(
                text(row.get("نوع السجل")) == "نوع قياسي"
                for row in launch_type_rows
            ),
            "typeAliases": sum(
                text(row.get("نوع السجل")) != "نوع قياسي"
                for row in launch_type_rows
            ),
            "brands": len({product["brand"] for product in products if product["brand"]}),
            "pricedOffers": sum(
                1 for row in cash_rows if isinstance(row.get("السعر الكاش"), (int, float))
            ),
            "completeInstallmentPlans": sum(
                1
                for row in installment_rows
                if text(row.get("اكتمال البيانات")) not in {"", "لم يبدأ"}
            ),
        },
        "sections": [
            {"name": name, "count": count}
            for name, count in sorted(
                section_counts.items(), key=lambda item: (-item[1], item[0])
            )
            if name
        ],
        "types": [
            {
                "name": name,
                "section": section_by_type.get(name, ""),
                "count": count,
            }
            for name, count in sorted(
                type_counts.items(), key=lambda item: (-item[1], item[0])
            )
            if name
        ],
        "products": products,
        "presence": presence,
        "sources": registry_sources,
    }

    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "products": len(products),
                "sources": len(source_rows),
                "selectedSectorStores": len(sectors_by_source),
                "mappings": len(mapping_rows),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
