"use client";

import { useActionState } from "react";
import { decideReviewItem, type ReviewActionState } from "./actions";

const initialState: ReviewActionState = { ok: false, message: "" };

export default function ReviewDecisionForm({
  reviewId,
  disabled = false,
}: {
  reviewId: string;
  disabled?: boolean;
}) {
  const [state, formAction, pending] = useActionState(
    decideReviewItem,
    initialState,
  );

  return (
    <form className="admin-review-form" action={formAction}>
      <input type="hidden" name="review_id" value={reviewId} />
      <input
        name="resolution"
        placeholder="سبب القرار أو الملاحظة"
        minLength={2}
        maxLength={2000}
        disabled={disabled || pending}
        required
      />
      <div>
        <button
          name="decision"
          value="resolved"
          disabled={disabled || pending}
        >
          اعتماد
        </button>
        <button
          name="decision"
          value="rejected"
          className="danger"
          disabled={disabled || pending}
        >
          رفض
        </button>
        <button
          name="decision"
          value="ignored"
          className="muted"
          disabled={disabled || pending}
        >
          تجاهل
        </button>
      </div>
      {state.message && (
        <small className={state.ok ? "success" : "error"}>{state.message}</small>
      )}
    </form>
  );
}
