"use server";

import { revalidatePath } from "next/cache";
import { getSa3arlyAdmin } from "./admin-auth";

export type ReviewActionState = {
  ok: boolean;
  message: string;
};

export async function decideReviewItem(
  _previous: ReviewActionState,
  formData: FormData,
): Promise<ReviewActionState> {
  const admin = await getSa3arlyAdmin();
  if (!admin) return { ok: false, message: "غير مصرح بتنفيذ هذا الإجراء." };

  const reviewId = String(formData.get("review_id") ?? "").trim();
  const decision = String(formData.get("decision") ?? "").trim();
  const resolution = String(formData.get("resolution") ?? "").trim();
  if (!/^[0-9a-fA-F-]{36}$/.test(reviewId)) {
    return { ok: false, message: "معرّف عنصر المراجعة غير صحيح." };
  }
  if (!new Set(["resolved", "rejected", "ignored"]).has(decision)) {
    return { ok: false, message: "قرار المراجعة غير صحيح." };
  }
  if (resolution.length < 2) {
    return { ok: false, message: "اكتب سبب القرار قبل الحفظ." };
  }

  const baseUrl = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  const token = process.env.SA3ARLY_INTERNAL_TOKEN;
  if (!baseUrl || !token) {
    return { ok: false, message: "اتصال لوحة الإدارة بالـAPI غير مضبوط." };
  }

  try {
    const response = await fetch(
      `${baseUrl}/internal/admin/review-queue/${reviewId}/decision`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Internal-Token": token,
        },
        body: JSON.stringify({
          decision,
          resolution,
          actor: admin.email,
        }),
        cache: "no-store",
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) {
      return { ok: false, message: `تعذر حفظ القرار (${response.status}).` };
    }
    revalidatePath("/admin");
    return { ok: true, message: "تم حفظ قرار المراجعة وتسجيله في سجل التدقيق." };
  } catch {
    return { ok: false, message: "تعذر الاتصال بخدمة الإدارة." };
  }
}
