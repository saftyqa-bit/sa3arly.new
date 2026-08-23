import type { Metadata } from "next";
import InfoPage from "../info-page";
import { absoluteUrl } from "../seo-data";

export const metadata: Metadata = {
  title: { absolute: "تواصل مع سعرلي" },
  alternates: { canonical: absoluteUrl("/contact") },
  robots: { index: false, follow: true },
};

export default function ContactPage() {
  return (
    <InfoPage
      eyebrow="التواصل والتصحيح"
      title="ساعدنا نخلي المقارنة أدق."
      intro="لا ننشر عنوان دعم غير معتمد. ستظهر قناة التواصل الرسمية هنا فور تفعيلها، من دون جمع بيانات عبر نموذج غير متصل."
    >
      <section>
        <h2>بلاغ عن سعر أو نسخة</h2>
        <p>
          جهّز اسم المنتج الدقيق، المتجر، رابط العرض، والسعر الذي ظهر لك مع وقت
          الملاحظة. هذه المعلومات تكفي لمراجعة الرصد من غير بيانات شخصية.
        </p>
      </section>
      <section>
        <h2>لأصحاب المتاجر</h2>
        <p>
          يمكن طلب تصحيح رابط أو تغيير حد الطلبات أو إيقاف الموصل. بعد اعتماد
          بريد الدعم سيظهر هنا وفي هوية الروبوت نفسها.
        </p>
      </section>
      <aside className="info-note">
        حالة القناة: غير مفعلة حاليًا. لا يوجد نموذج يرسل بيانات في النسخة
        الحالية.
      </aside>
    </InfoPage>
  );
}
