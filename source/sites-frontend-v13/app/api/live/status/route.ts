import { cloudRunAuthorizationHeaders } from "../../../cloud-run-auth";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
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
  try {
    const response = await fetch(`${apiBase}/api/v1/status`, {
      headers: {
        Accept: "application/json",
        ...(await cloudRunAuthorizationHeaders(apiBase)),
      },
      cache: "no-store",
    });
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
