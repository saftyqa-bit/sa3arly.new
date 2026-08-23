import { NextRequest, NextResponse } from "next/server";
import { forwardLive, validVariantId } from "../../../proxy";

export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ variantId: string }> },
) {
  const { variantId } = await context.params;
  if (!validVariantId(variantId)) {
    return NextResponse.json(
      { detail: "Invalid variant ID." },
      { status: 400, headers: { "X-Robots-Tag": "noindex, nofollow" } },
    );
  }
  return forwardLive(`/api/v1/products/${encodeURIComponent(variantId)}/decision`);
}
