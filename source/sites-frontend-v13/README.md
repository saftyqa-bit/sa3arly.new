# Sa3arly Web — Next.js standalone

واجهة سعرلي العامة ولوحة الإدارة مبنيتان على **Next.js App Router** وتخرجان كحزمة
`standalone` قابلة للتشغيل على Google Cloud Run. لا تعتمد هذه النسخة على Vinext أو
Vite أو Cloudflare Workers، ولذلك لا تتأثر بسحب إصدارات الحزم التجريبية القديمة.

## التشغيل المحلي

```bash
npm run install:ci
npm run dev
```

## التحقق الكامل

```bash
npm run typecheck
npm test
```

`npm test` ينفذ بناء Production، يتحقق من حزمة `.next/standalone`، يشغل خادم
Next.js الفعلي، ثم يختبر الصفحات وبيانات SEO وخرائط الموقع وحالات الأسعار الوهمية.

## متغيرات التشغيل

```env
SA3ARLY_API_BASE_URL=https://sa3arly-api-...run.app
SA3ARLY_INTERNAL_TOKEN=server-only-token
SA3ARLY_ADMIN_EMAILS=owner@example.com
```

- `SA3ARLY_API_BASE_URL` غير سري ويستخدم لقراءة الأسعار الحية.
- `SA3ARLY_INTERNAL_TOKEN` سري ويجب حقنه من Google Secret Manager فقط.
- بيانات لوحة `/admin` لا تصل إلى المتصفح مباشرة؛ الطلبات الداخلية تتم من الخادم.

## Docker / Cloud Run

```bash
docker build -t sa3arly-web .
docker run --rm -p 8080:8080 \
  -e SA3ARLY_API_BASE_URL=https://example.run.app \
  sa3arly-web
```

الـDockerfile يعمل بمستخدم غير root ويشغّل `server.js` الناتج من Next.js standalone.
