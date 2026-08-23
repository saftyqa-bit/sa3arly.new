import type { Metadata } from "next";
import InfoPage from "../info-page";
import { absoluteUrl } from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "سياسة روبوت سعرلي" },
  description: "هوية وقواعد عمل Sa3arlyPriceBot وطلبات التصحيح أو الإيقاف.",
  alternates: { canonical: absoluteUrl("/bot") },
  robots: { index: false, follow: true },
};

export default function BotPolicyPage() {
  return (
    <InfoPage
      eyebrow="Sa3arlyPriceBot/1.0"
      title="سياسة روبوت سعرلي"
      intro="يجمع الروبوت معلومات السعر والتوفر والشحن والتقسيط الظاهرة للعامة لمساعدة المستخدم على المقارنة، مع تشغيل موزع وحدود مستقلة لكل متجر."
    >
      <section>
        <h2>قواعد الوصول</h2>
        <ul>
          <li>احترام robots.txt وحدود السرعة المعلنة أو المتفق عليها.</li>
          <li>عدم تجاوز تسجيل الدخول أو CAPTCHA أو أي حاجز وصول.</li>
          <li>عدم استخدام API خاص أو مدفوع من غير إذن.</li>
          <li>عدم إعادة تحميل الرابط نفسه لكل منتج داخل الدورة الواحدة.</li>
          <li>عدم نشر سعر لم ينجح استخراجه ومطابقته.</li>
        </ul>
      </section>
      <section>
        <h2>التصحيح أو إيقاف الموصل</h2>
        <p>
          يقدر مالك المتجر يطلب تصحيح رابط أو تقليل معدل الطلبات أو إيقاف
          الموصل من صفحة التواصل. قناة البريد الرسمية ستُثبت في الصفحة قبل
          الإطلاق العام.
        </p>
        <a className="info-action" href="/contact">
          افتح صفحة التواصل ←
        </a>
      </section>
      <section lang="en" dir="ltr">
        <h2>English summary</h2>
        <p>
          Sa3arlyPriceBot/1.0 collects publicly visible comparison data. It
          respects robots.txt and per-store rate limits, does not bypass access
          controls, and publishes no price before a successful product match.
        </p>
      </section>
      <p className="info-updated">آخر تحديث للسياسة: 30 يوليو 2026.</p>
    </InfoPage>
  );
}
