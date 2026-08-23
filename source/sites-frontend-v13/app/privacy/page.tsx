import type { Metadata } from "next";
import InfoPage from "../info-page";
import { absoluteUrl } from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "سياسة الخصوصية | سعرلي" },
  alternates: { canonical: absoluteUrl("/privacy") },
  robots: { index: false, follow: true },
};

export default function PrivacyPage() {
  return (
    <InfoPage
      eyebrow="الخصوصية"
      title="بيانات أقل، ووضوح أكبر."
      intro="واجهة المقارنة الحالية لا تطلب إنشاء حساب ولا تجمع بيانات دفع أو ملفات شخصية."
    >
      <section>
        <h2>ما الذي تستخدمه النسخة الحالية؟</h2>
        <p>
          استعلام البحث واختيار القسم يظلان داخل تجربة المقارنة. لا توجد حسابات
          مستخدمين أو تنبيهات محفوظة في هذه النسخة، ولا تُرسل بيانات إلى
          المتاجر إلا عند فتح رابط العرض بنفسك.
        </p>
      </section>
      <section>
        <h2>القياس التشغيلي</h2>
        <p>
          نستخدم Google Analytics لقياس زيارات الصفحات والتفاعل العام مع
          الموقع، مثل نوع الجهاز والمتصفح والمنطقة التقريبية ومسارات التنقل.
          تساعدنا هذه البيانات المجمعة على تحسين الأداء وتجربة المقارنة، ولا
          نرسل إلى الأداة بيانات دفع أو ملفات مستخدمين.
        </p>
      </section>
      <section>
        <h2>أسعار المتاجر</h2>
        <p>
          بيانات الأسعار تخص عروضًا عامة وليست بيانات شخصية. يحتفظ المحرك بوقت
          الرصد والمصدر وحالة الاستخراج لأغراض التحقق والجودة.
        </p>
      </section>
    </InfoPage>
  );
}
