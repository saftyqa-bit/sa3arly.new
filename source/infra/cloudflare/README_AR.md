# توجيه واجهة سعرلي عبر Cloudflare

هذه الطبقة توجّه `sa3arly.com` و`www.sa3arly.com` إلى خدمة الويب المنشورة على
Cloud Run من دون تغيير سجلات DNS أو حذف الاستضافة السابقة.

## النشر

```powershell
npx wrangler deploy --config infra/cloudflare/wrangler.jsonc
```

## التحقق

```powershell
curl.exe -I https://sa3arly.com/
curl.exe -I https://www.sa3arly.com/api/live/status
```

يجب أن تحتوي الاستجابة على:

```text
x-sa3arly-edge: cloudflare-web-proxy-v1
```

## الرجوع الفوري

حذف مساري Worker يعيد حركة الدومين إلى الأصل السابق الموجود في DNS؛ لا يتم حذف
ذلك الأصل عند نشر هذا الـWorker. من لوحة Cloudflare افتح **Workers Routes** واحذف
المسارين التاليين فقط مع إبقاء سجلات DNS كما هي:

```text
sa3arly.com/*
www.sa3arly.com/*
```
