# نشر سعرلي على Google Cloud

المسار الإنتاجي هو `deploy.sh` ويجهز Cloud SQL PostgreSQL 16، وCloud Run API
وWorker، وCloud Tasks، وCloud Scheduler عند `00:00` و`12:00` بتوقيت القاهرة.

الأسهل من Cloud Shell:

```bash
bash DEPLOY_SA3ARLY_CLOUD_SHELL.sh
```

أو مباشرة مع المتغيرات:

```bash
export PROJECT_ID="sa3arly-prod-972741"
export DB_PASSWORD="strong-generated-password"
export BOT_CONTACT_URL="https://sa3arly.com/bot"
export ALLOWED_ORIGINS="https://sa3arly.com"
export REGION="europe-west1"
bash infra/gcp/deploy.sh
```

يمكن استخدام PostgreSQL قائم عبر `CREATE_CLOUD_SQL=0` مع تجهيز Secret باسم
`sa3arly-database-url`. النشر لا يفعّل الجمع. التفعيل المنفصل:

```bash
COST_CONTROLS_CONFIRMED=1 bash START_SA3ARLY_COLLECTION.sh
```

`deploy-firestore.sh` مسار رجوع قديم محمي ولا يعمل إلا عند تعيين
`ALLOW_LEGACY_FIRESTORE_DEPLOY=1` صراحة.
