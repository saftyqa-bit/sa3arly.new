import { NextRequest, NextResponse } from "next/server";
import { cloudRunAuthorizationHeaders } from "../../../../../cloud-run-auth";

export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ variantId: string }> },
) {
  const apiBase = process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "");
  if (!apiBase) {
    return NextResponse.json(
      { detail: "Live price engine is not connected yet." },
      {
        status: 503,
        headers: { "X-Robots-Tag": "noindex, nofollow" },
      },
    );
  }
  const { variantId } = await context.params;
  if (!/^VAR-[A-Z0-9]{6,40}$/.test(variantId)) {
    return NextResponse.json(
      { detail: "Invalid variant ID." },
      {
        status: 400,
        headers: { "X-Robots-Tag": "noindex, nofollow" },
      },
    );
  }
  try {
    const response = await fetch(
      `${apiBase}/api/v1/products/${encodeURIComponent(variantId)}/comparison`,
      {
        headers: {
          Accept: "application/json",
          ...(await cloudRunAuthorizationHeaders(apiBase)),
        },
        cache: "no-store",
      },
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-Robots-Tag": "noindex, nofollow",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Live price engine is temporarily unavailable." },
      {
        status: 502,
        headers: { "X-Robots-Tag": "noindex, nofollow" },
      },
    );
  }
}
