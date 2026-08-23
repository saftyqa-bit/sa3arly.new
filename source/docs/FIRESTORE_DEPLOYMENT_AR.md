# مسار الرجوع القديم إلى Firestore Standard

هذا الملف للتعافي فقط. الإنتاج المعتمد يستخدم PostgreSQL عبر
`infra/gcp/deploy.sh`. لا يحذف مسار النقل Firestore، لذلك يمكن إعادة توجيه
الترافيك إلى الـRevisions السابقة من دون استيراد عكسي.

إذا كان لابد من إعادة نشر Backend كامل على Firestore:

1. أوقف `sa3arly-scrape` و`sa3arly-12h-refresh`.
2. تحقق أن بيانات Firestore المصدرية ما زالت موجودة.
3. نفّذ فقط بقرار رجوع صريح:

```bash
export ALLOW_LEGACY_FIRESTORE_DEPLOY=1
export PROJECT_ID="sa3arly-prod-972741"
export BOT_CONTACT_URL="https://sa3arly.com/bot"
export ALLOWED_ORIGINS="https://sa3arly.com"
bash infra/gcp/deploy-firestore.sh
```

المسار القديم مضبوط أيضًا على `0 0,12 * * *` و`Africa/Cairo`، لكنه لا يعيد
تفعيل الجمع تلقائيًا ما لم تُمرر موافقة التكلفة. احتفظ بقاعدة PostgreSQL
وسرها أثناء الرجوع حتى يمكن استعادة المسار الرئيسي.
