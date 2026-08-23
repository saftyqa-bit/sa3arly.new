import type { Metadata } from "next";
import InfoPage from "../info-page";
import { absoluteUrl } from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "شروط الاستخدام | سعرلي" },
  alternates: { canonical: absoluteUrl("/terms") },
  robots: { index: false, follow: true },
};

export default function TermsPage() {
  return (
    <InfoPage
      eyebrow="شروط الاستخدام"
      title="المقارنة تساعدك، والمتجر يحسم العملية."
      intro="سعرلي أداة معلومات ومقارنة وليست متجرًا أو جهة تمويل أو طرفًا في عملية البيع."
    >
      <section>
        <h2>السعر النهائي</h2>
        <p>
          السعر والتوفر والشحن والضمان وشروط التقسيط قد تتغير في صفحة المتجر.
          راجع المصدر قبل الشراء، خصوصًا إذا كان الرصد متأخرًا أو الشحن غير
          معلوم.
        </p>
      </section>
      <section>
        <h2>التقسيط</h2>
        <p>
          عرض الخطة لا يعني قبول التمويل. الأهلية والبطاقة والحد الائتماني
          والرسوم النهائية تخضع للمتجر أو البنك أو مقدم الخدمة.
        </p>
      </section>
      <section>
        <h2>التصحيحات</h2>
        <p>
          لو لقيت اختلافًا، أرسل رابط المنتج واسم النسخة والمتجر من صفحة
          التواصل. السجل يحتفظ بالمصدر ووقت الرصد لتسهيل المراجعة.
        </p>
      </section>
    </InfoPage>
  );
}
