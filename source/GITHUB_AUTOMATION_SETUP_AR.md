# ربط Sa3arly بـGitHub وGoogle Cloud بدون مفاتيح دائمة

أصبحت النسخة مجهزة لتنفيذ التسلسل التالي عند كل Push إلى `main`:

1. اختبار Python والتحقق من Seed وسعة دورة الأسعار.
2. تثبيت Next.js من lockfile نظيف وبناء حزمة `standalone`.
3. تشغيل اختبارات HTML وSEO على خادم Production فعلي.
4. المصادقة من GitHub إلى Google Cloud عبر OIDC وWorkload Identity Federation.
5. إنشاء Backup فوري لـCloud SQL وإيقاف الـSchedulers والـQueues مؤقتًا.
6. تشغيل Migrations بقفل PostgreSQL وChecksum دائم.
7. نشر API والـWorker وإجراء Health Checks مع Rollback عند الفشل.
8. نشر واجهة Next.js على خدمة مستقلة باسم `sa3arly-web` دون تغيير توجيه الدومين الحالي.

## أمر التهيئة الواحد

بعد فك الحزمة داخل Google Cloud Shell، ومن جذر المشروع:

```bash
SA3ARLY_ADMIN_EMAILS="YOUR_ADMIN_EMAIL" \
bash scripts/bootstrap_autonomous_delivery.sh
```

السكربت يقوم بالآتي بالترتيب الآمن:

- ينشئ مستودعًا خاصًا باسم `engmohamedelmorsy-arch/sa3arly` إن لم يكن موجودًا.
- ينشئ أول Commit محليًا.
- ينشئ Service Account محدودًا للنشر.
- يربط GitHub بالمشروع `sa3arly-prod-972741` دون Service Account JSON.
- يضيف GitHub Repository Variables قبل أول Push.
- يرفع `main` ويبدأ أول Workflow تلقائيًا.

يلزم أن يكون `gh` و`gcloud` مسجلين في Cloud Shell. لا تُرسل Tokens أو مفاتيح JSON داخل المحادثة.

## خطوة حساب واحدة لا يستطيع الكود تنفيذها

بعد إنشاء المستودع، افتح إعدادات GitHub ثم:

`Settings → Applications → Installed GitHub Apps → ChatGPT → Configure`

وأضف مستودع `sa3arly` إلى المستودعات المسموح بها. هذه الخطوة تمنحني القدرة على قراءة الملفات وتعديلها ورفع Commits لاحقًا؛ أما GitHub Actions والنشر التلقائي فيعملان بعد السكربت حتى قبل هذه الخطوة.

## الأمان

- Workload Identity مقيد بالمستودع المحدد وفرع `main` فقط.
- لا توجد مفاتيح Google Cloud طويلة الأجل.
- قاعدة البيانات تُنسخ احتياطيًا قبل Migration.
- كل Migration لها Checksum ولا يمكن تعديل Migration مطبقة سابقًا بصمت.
- نشر الواجهة ينشئ خدمة معاينة مستقلة ولا يغير DNS أو حركة `sa3arly.com`.
