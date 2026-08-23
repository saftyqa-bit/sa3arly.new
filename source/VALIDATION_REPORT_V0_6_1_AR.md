# تقرير التحقق — Sa3arly v0.6.1

## فحوص نجحت في بيئة العمل الحالية

- قراءة جميع ملفات JSON وYAML الرئيسية دون أخطاء بنيوية.
- فحص Bash syntax لسكربتات النشر والتهيئة والـRollback.
- Python `compileall` لكل `app` و`scripts` و`tests`.
- اختبارات الوحدات التي لا تحتاج اتصال PostgreSQL، بما فيها Migration contract والنموذج Product-Centric.
- `verify_seed.py`: 2,471 منتجًا، 216 متجرًا، و2,360 Mapping.
- `verify_hourly_capacity.py`: جميع المتاجر الملحقة تقع داخل نافذة 45 دقيقة.
- `verify_catalog_discovery_readiness.py`: لا توجد مشكلات جاهزية في Registry.
- npm lockfile dry-run: 98 حزمة قابلة للتثبيت، دون Vinext أو Vite أو Wrangler أو Cloudflare Vite Plugin.
- فحص ثابت لعدم وجود Private Keys أو GitHub/OpenAI tokens أو كلمات مرور قاعدة بيانات داخل المستودع.
- `git diff --check` و`git fsck`.

## فحوص ستنفذها GitHub Actions

تعذر إكمال `npm ci` الفعلي داخل شبكة بيئة العمل لأن مرآة npm الداخلية لا تحتوي على
`undici-types`، رغم أن lockfile أصبح سليمًا ولا يطلب Vinext المسحوب. GitHub Actions
سيستخدم npm العام ويشغّل البناء وRendered HTML tests تلقائيًا.

كما تعذر تشغيل مجموعة Python الكاملة محليًا لأن حزمة `psycopg` غير مثبتة في النظام
ولا يمكن تنزيلها من الشبكة الحالية. الـWorkflow يثبت `requirements-dev.txt` قبل
تشغيل `ruff` و`pytest`، بينما نجحت الاختبارات المستقلة المتاحة محليًا.

## الإنتاج

- لم تُعدل قاعدة Cloud SQL الحالية من بيئة العمل.
- لم يتغير DNS أو توجيه `sa3arly.com`.
- أول Push بعد تشغيل سكربت التهيئة سيبدأ CI، ثم Backup وMigration ونشر API وWorker،
  ثم ينشئ خدمة معاينة مستقلة باسم `sa3arly-web`.
