# دليل إضافة متجر جديد إلى محرك سعرها

## الهدف

كل متجر له سجل في `merchant.stores` وإعداد واحد في `merchant.connector_configs`. ينشئ محرك
الكتالوج مصدرًا من `base_url` للمتاجر النشطة، لكن مهام الأسعار لا تبدأ إلا بعد
مطابقة رابط منتج مؤكد وإنشاء سجل في `merchant.listings` ورابط رئيسي في
`merchant.listing_urls`.

## ترتيب طرق الاستخراج

1. API عام مجاني ومسموح.
2. JSON-LD داخل صفحة المنتج.
3. JSON مضمّن أو feed عام.
4. HTML selectors خاصة بالمتجر.
5. Playwright للصفحات التي لا تكتمل إلا بجافاسكربت.

لا يتجاوز النظام تسجيل دخول أو CAPTCHA أو API خاصًا/مدفوعًا.

## إعداد الموصل

الحقل `config` يدعم، عند الحاجة:

```json
{
  "location": "Cairo",
  "currency": "EGP",
  "cardSelectors": [".product-card"],
  "titleSelectors": [".product-title"],
  "priceSelectors": ["[itemprop=price]", ".sale-price"],
  "oldPriceSelectors": [".old-price"],
  "availabilitySelectors": [".stock-status"],
  "linkSelectors": ["a.product-link"],
  "skuSelectors": ["[data-sku]"],
  "installmentSelectors": [".installment-modal", "#payment-plans"],
  "providerAliases": {
    "اسم مختصر داخل المتجر": {"name": "الاسم الموحد للجهة", "type": "consumer_finance"}
  },
  "discoverySources": [
    "https://store.example/sitemap.xml",
    "https://store.example/en/products"
  ],
  "browserActions": [
    {"action": "click", "selector": "button.installments", "optional": true},
    {"action": "wait_for_selector", "selector": ".installment-modal", "timeout_ms": 3000}
  ]
}
```

`discoverySources` اختيارية؛ تُستخدم عندما يكون Sitemap أو Feed المعلن أدق من
صفحة البداية. يجب أن تبقى الروابط داخل `allowed_hosts`.

## حقول الربط الإلزامية

- `mapping_id`
- `offer_id`
- `offer_key`
- `variant_id`
- `store_id`
- `source_url`
- `url_type`
- `title_as_seen`
- `match_confidence`
- `active`

## صفحات الفئات وFeeds

إذا كانت صفحة الفئة أو الـfeed تعرض السعر، يحتفظ النظام بها كمصدر الرصد الدوري، مع حفظ رابط المنتج المباشر للشراء. هذا يمنع تحويل مئات المنتجات إلى مئات طلبات منفصلة بلا حاجة.

إذا كانت صفحة الفئة لا تعرض السعر، يحفظ النظام رابط المنتج المكتشف ويجعله مصدر الرصد في الدورات التالية.

## ضبط السرعة

- `requests_per_minute`: حد آمن منفصل لكل متجر.
- `respect_robots`: يبقى `true` افتراضيًا.
- `browser_required`: لا يُفعّل إلا عند ثبوت الحاجة.
- `max_concurrency`: عدد فتحات التنفيذ المتزامن المسموح بها للمتجر عبر جميع نسخ الـWorker.
- `browserActions`: نقر/انتظار/اختيار آمن ومحدود دون تنفيذ JavaScript مخصص، لفتح نافذة التقسيط العامة عند الحاجة.
- `providerAliases`: إضافة أي بنك أو شركة تمويل جديدة من الإعدادات دون تعديل الكود.
- `enabled`: يوقف الموصل فورًا دون حذف البيانات.

شغّل قبل النشر:

```bash
python scripts/verify_hourly_capacity.py
```

إذا فشل الاختبار، يجب زيادة الاعتماد على feeds/صفحات الفئات أو تخفيض عدد الروابط النشطة أو ضبط معدل آمن أعلى بعد الاختبار.

## اعتماد السعر

لا يعتمد السعر إلا إذا:

- تطابق الموديل والنسخة.
- لم يوجد تعارض في السعة أو الرام.
- السعر موجب.
- لم يكن تغيره شاذًا جدًا مقارنة بآخر سعر ناجح.
- ظل رابط المصدر عامًا وصالحًا.

السعر الشاذ لا يستبدل السعر السابق؛ يسجل كـ`price_anomaly` للمراجعة.

## التقسيط

كل جهة ومدة هي خطة مستقلة. لا تدمج الخطط في خلية واحدة. عرض «يبدأ من» يحمل `starting_from_only=true` ولا يعامل كخطة مكتملة عند الترتيب.
