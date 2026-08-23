import { NextRequest } from "next/server";
import { forwardLive } from "../proxy";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.get("q")?.slice(0, 200) || "";
  const limit = Math.max(1, Math.min(Number(request.nextUrl.searchParams.get("limit") || 20), 50));
  const params = `q=${encodeURIComponent(query)}&limit=${limit}`;
  return forwardLive(
    `/api/v1/products/search/smart?${params}`,
    {},
    `/api/v1/products/search?${params}`,
  );
}
