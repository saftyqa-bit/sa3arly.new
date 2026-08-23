# سعرلي — محرك اكتشاف الكتالوج والأسعار 0.5.1

هذه حزمة الـBackend الإنتاجية لسعرلي: Cloud Run للـAPI والـWorker، وCloud
Tasks بطابورين مستقلين، وCloud Scheduler، وCloud SQL for PostgreSQL. تحديث
الأسعار يعمل يوميًا الساعة 10:00 صباحًا و8:00 مساءً بتوقيت القاهرة، بينما
يعمل اكتشاف الكتالوج في دورة ليلية مستقلة.

## الحالة المعتمدة قبل الاكتشاف

- 2,471 نسخة منتج أساسية و4 aliases توافقية.
- 216 متجرًا في السجل؛ 209 نشطة و7 محفوظة كغير نشطة.
- 2,332 ربطًا تشغيليًا قائمًا.
- 14 متجرًا لديها روابط تشغيلية حاليًا.
- 195 متجرًا نشطًا متبقيًا يُنشئ لها المحرك مصادر اكتشاف آمنة.

## ماذا يفعل محرك الكتالوج

1. ينشئ مصدرًا لكل متجر نشط من `base_url` وأي `discoverySources` مهيأة.
2. يقرأ `robots.txt` وSitemap/feeds وصفحات الفئات العامة فقط.
3. يزيل روابط التتبع ويمنع الروابط الخارجية والملفات وصفحات الحساب/الدفع.
4. يطابق الرابط مع النسخة الصحيحة باستخدام GTIN/SKU والموديل والسعة والرام
   واللون، مع هامش يمنع المطابقة الغامضة.
5. ينشئ `store_product_mapping` وعرض كاش مبدئي ومهمة تقسيط منفصلة عند الثقة
   العالية فقط؛ دورة الأسعار التالية هي التي تجلب السعر الفعلي.
6. يحتفظ بالحالات الأضعف داخل `catalog_candidates` للمراجعة ولا ينشرها.
7. المنتج الجديد ذو GTIN يبقى `catalog_provisional` وغير ظاهر. لا يصبح
   `catalog_verified` إلا بعد تأكيده من متجرين مختلفين.

لا يتجاوز المحرك تسجيل دخول أو CAPTCHA، ولا يستخدم API خاصًا/مدفوعًا، ولا
يعتبر المتجر متصلًا لمجرد وجوده في السجل. يزيد `connected_stores` فقط بعد
إنشاء ربط مؤكد فعليًا.

## الدورات

- الأسعار: `0 10,20 * * *` في `Africa/Cairo`.
- اكتشاف الكتالوج: `30 2 * * *` في `Africa/Cairo`.
- 35 متجرًا في الليلة؛ أول مرور على 209 متاجر يستغرق نحو 6 ليالٍ.
- إعادة فحص المصدر الناجح أسبوعيًا، مع تأجيل مستقل للحظر ومحددات السرعة.
- طابور الأسعار `sa3arly-scrape` لا يتغير.
- طابور الاكتشاف `sa3arly-catalog-discovery` يعمل بتزامن واحد و0.2 مهمة/ثانية.

## النشر الآمن على الإنتاج الحالي

```bash
chmod +x DEPLOY_CATALOG_DISCOVERY.sh \
  VERIFY_CATALOG_DISCOVERY.sh \
  ACTIVATE_CATALOG_DISCOVERY.sh

./DEPLOY_CATALOG_DISCOVERY.sh
```

السكربت يثبت Migration `004`، ينشر API/Worker، ينشئ الطابور والمجدول الجديد،
ويشغّل Canary على 5 متاجر. يبقى مجدول الكتالوج `PAUSED` ولا يغيّر موعد
الأسعار.

بعد تفريغ مهام الـCanary:

```bash
./VERIFY_CATALOG_DISCOVERY.sh
./ACTIVATE_CATALOG_DISCOVERY.sh
```

لا تستخدم `ENABLE_HOURLY_COLLECTION.sh` أو `VERIFY_HOURLY_COLLECTION.sh`؛
تم تعطيلهما في هذا الإصدار لمنع إعادة النظام إلى التحديث الساعي.

## المسارات الجديدة

```text
POST /internal/scheduler/catalog-discovery
POST /internal/tasks/catalog-discovery
GET  /internal/catalog/runs/{run_id}
```

يعرض `/api/v1/status` أيضًا أعداد مصادر الكتالوج، المرشحين، حالات المراجعة،
والمنتجات المبدئية والمؤكدة، إلى جانب أرقام الأسعار الحالية.

## التحقق المحلي

```bash
python scripts/verify_seed.py
python scripts/verify_hourly_capacity.py
python scripts/verify_catalog_discovery_readiness.py
ruff check app scripts tests
pytest
python -m compileall -q app scripts tests
```

راجع `CATALOG_DISCOVERY_RELEASE_NOTES_AR.md` و`docs/ARCHITECTURE_AR.md`.

## Core V2: كتالوج المنتج ولوحة الإدارة

أضيف خط أساس PostgreSQL نظيف في `db/migrations/001_sa3arly_core_v2.sql` ويقسم
المسؤوليات إلى نطاقات واضحة، مع مسار المنتج التالي:

```text
تصنيف ← ماركة ← عائلة ← منتج ← نسخة ← listing متجر ← سعر حالي + رصد تاريخي
```

يُنشأ المخطط في namespaces مستقلة بجانب جداول `public` القديمة، ثم تنقل البيانات
داخل transaction واحدة وتُراجع الأعداد قبل تحويل الاتصال. لا تُحذف جداول
الإنتاج القديمة أثناء الـcutover حتى يظل الرجوع ممكنًا. توجد صفحة `/admin`
ومؤشرات جودة البيانات وقائمة مراجعة وسجل تدقيق. تفاصيل القرار في
`docs/ADR_0011_SA3ARLY_CORE_V2_DATABASE.md`.
