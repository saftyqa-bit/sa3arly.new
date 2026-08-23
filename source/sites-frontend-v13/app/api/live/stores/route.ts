import { NextRequest } from "next/server";
import { forwardLive } from "../proxy";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.get("q")?.trim().slice(0, 120);
  const path = query
    ? `/api/v1/stores?q=${encodeURIComponent(query)}&limit=500`
    : "/api/v1/stores?limit=500";
  return forwardLive(path);
}
