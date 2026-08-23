# Sa3arly v0.6.1 — Autonomous GitHub and Google Cloud delivery

## قاعدة البيانات والتشغيل

- CI موحد للباك إند والواجهة.
- نشر تلقائي من `main` باستخدام Workload Identity Federation دون مفاتيح JSON.
- Migration ledger دائم باسم `schema_migrations`.
- SHA-256 checksum يمنع تعديل Migration مطبقة سابقًا.
- PostgreSQL advisory lock يمنع نشرين متزامنين من تغيير الـSchema.
- Backup لـCloud SQL وRollback وحفظ حالة Cloud Scheduler وCloud Tasks.
- Dependabot لـPython وnpm وGitHub Actions.

## الواجهة

- استبدال Vinext التجريبي المسحوب من npm بـNext.js 16 القياسي.
- حذف Vite وWrangler وCloudflare Worker/D1 من مسار الإنتاج.
- تقليل lockfile من أكثر من 700 مدخل إلى 105 تقريبًا، دون Vinext أو Vite.
- حزمة Next.js `standalone` وDockerfile يعمل بمستخدم غير root.
- اختبارات Rendered HTML تشغّل خادم Production الفعلي.
- خدمة Cloud Run مستقلة `sa3arly-web` لا تغيّر الدومين الحالي.
- دعم استدعاء Cloud Run الخاص باستخدام ID token من Metadata Server دون مكتبات أو مفاتيح إضافية.

## التهيئة

- `scripts/bootstrap_autonomous_delivery.sh`: إنشاء المستودع، ربط WIF، ضبط Variables، ثم أول Push.
- `scripts/bootstrap_github_wif.sh`: صلاحيات Google Cloud المقيدة بالمستودع وفرع `main`.
- `scripts/deploy_web_cloud_run.sh`: بناء ونشر معاينة الواجهة مع Health Checks.

## ما لا يتغير

- لا تُحذف قاعدة الإنتاج الحالية أو بيانات الأسعار.
- أسماء API وWorker وCloud SQL ومشروع Google Cloud والمنطقة تبقى كما هي.
- لا يتحول `sa3arly.com` إلى الخدمة الجديدة تلقائيًا؛ يتم ذلك فقط بعد مراجعة المعاينة.
