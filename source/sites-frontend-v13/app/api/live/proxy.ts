import { NextResponse } from "next/server";
import { cloudRunAuthorizationHeaders } from "../../cloud-run-auth";

function apiBase() {
  return process.env.SA3ARLY_API_BASE_URL?.replace(/\/+$/, "") || null;
}

export async function forwardLive(
  path: string,
  init: RequestInit = {},
  fallbackPath?: string,
): Promise<NextResponse> {
  const base = apiBase();
  if (!base) {
    return NextResponse.json(
      { detail: "Live price engine is not connected yet." },
      { status: 503, headers: { "X-Robots-Tag": "noindex, nofollow" } },
    );
  }
  try {
    const headers = {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(await cloudRunAuthorizationHeaders(base)),
      ...(init.headers || {}),
    };
    let response = await fetch(`${base}${path}`, {
      ...init,
      cache: "no-store",
      headers,
    });
    if (!response.ok && fallbackPath && !init.body) {
      response = await fetch(`${base}${fallbackPath}`, {
        ...init,
        cache: "no-store",
        headers,
      });
    }
    const payload = await response.text();
    const responseType = response.headers.get("content-type") || "";
    const looksLikeJson = responseType.includes("application/json")
      || /^[\s\r\n]*[\[{]/.test(payload);
    if (!looksLikeJson) {
      return NextResponse.json(
        {
          detail: response.ok
            ? "Live price engine returned an invalid response."
            : "Live price engine could not complete this request.",
        },
        {
          status: response.ok ? 502 : response.status,
          headers: { "X-Robots-Tag": "noindex, nofollow" },
        },
      );
    }
    return new NextResponse(payload, {
      status: response.status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-Robots-Tag": "noindex, nofollow",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Live price engine is temporarily unavailable." },
      { status: 502, headers: { "X-Robots-Tag": "noindex, nofollow" } },
    );
  }
}

export function validVariantId(value: string) {
  return /^VAR-[A-Z0-9]{6,40}$/.test(value);
}
