import { NextRequest } from "next/server";
import { forwardLive } from "../proxy";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const body = await request.text();
  return forwardLive("/api/v1/alerts", { method: "POST", body });
}
