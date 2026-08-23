const metadataIdentityEndpoint =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";

type CachedToken = { token: string; expiresAt: number };
const tokenCache = new Map<string, CachedToken>();

function tokenExpiry(token: string) {
  try {
    const payload = token.split(".")[1];
    if (!payload) return Date.now() + 45 * 60_000;
    const normalized = payload.replaceAll("-", "+").replaceAll("_", "/");
    const parsed = JSON.parse(
      Buffer.from(normalized, "base64").toString("utf8"),
    ) as { exp?: number };
    return parsed.exp ? parsed.exp * 1000 : Date.now() + 45 * 60_000;
  } catch {
    return Date.now() + 45 * 60_000;
  }
}

/**
 * Returns an ID-token Authorization header when running on Google Cloud Run.
 * Outside Google Cloud the metadata lookup fails closed and public/local APIs
 * continue to work without cloud credentials.
 */
export async function cloudRunAuthorizationHeaders(
  audience: string,
): Promise<Record<string, string>> {
  const cached = tokenCache.get(audience);
  if (cached && cached.expiresAt - Date.now() > 60_000) {
    return { Authorization: `Bearer ${cached.token}` };
  }

  try {
    const endpoint = new URL(metadataIdentityEndpoint);
    endpoint.searchParams.set("audience", audience);
    endpoint.searchParams.set("format", "full");
    const response = await fetch(endpoint, {
      headers: { "Metadata-Flavor": "Google" },
      cache: "no-store",
      signal: AbortSignal.timeout(2_000),
    });
    if (!response.ok) return {};
    const token = (await response.text()).trim();
    if (!token) return {};
    tokenCache.set(audience, { token, expiresAt: tokenExpiry(token) });
    return { Authorization: `Bearer ${token}` };
  } catch {
    return {};
  }
}
