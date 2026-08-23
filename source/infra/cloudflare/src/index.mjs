const DEFAULT_ORIGIN = "https://sa3arly-web-oitr3qtggq-ew.a.run.app";

function webOrigin(env) {
  const origin = new URL(env?.SA3ARLY_WEB_ORIGIN || DEFAULT_ORIGIN);

  if (origin.protocol !== "https:") {
    throw new Error("SA3ARLY_WEB_ORIGIN must use HTTPS");
  }

  return origin;
}

function upstreamRequest(request, origin) {
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(origin);
  targetUrl.pathname = incomingUrl.pathname;
  targetUrl.search = incomingUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("x-forwarded-host", incomingUrl.host);
  headers.set("x-forwarded-proto", incomingUrl.protocol.slice(0, -1));
  headers.set("x-sa3arly-edge", "cloudflare");

  const init = {
    method: request.method,
    headers,
    redirect: "manual",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  return new Request(targetUrl, init);
}

function publicLocation(location, origin, incomingUrl) {
  if (!location) return location;

  const redirectUrl = new URL(location, origin);
  if (redirectUrl.origin !== origin.origin) return location;

  redirectUrl.protocol = incomingUrl.protocol;
  redirectUrl.host = incomingUrl.host;
  return redirectUrl.toString();
}

export default {
  async fetch(request, env) {
    const incomingUrl = new URL(request.url);

    try {
      const origin = webOrigin(env);
      const upstream = await fetch(upstreamRequest(request, origin));
      const response = new Response(upstream.body, upstream);
      const location = response.headers.get("location");

      if (location) {
        response.headers.set(
          "location",
          publicLocation(location, origin, incomingUrl),
        );
      }

      response.headers.set("x-sa3arly-edge", "cloudflare-web-proxy-v1");
      return response;
    } catch {
      return new Response(
        JSON.stringify({
          ok: false,
          error: "تعذر الوصول إلى خدمة سعرلي مؤقتًا.",
        }),
        {
          status: 502,
          headers: {
            "cache-control": "no-store",
            "content-type": "application/json; charset=utf-8",
            "x-sa3arly-edge": "cloudflare-web-proxy-v1",
          },
        },
      );
    }
  },
};

export { publicLocation, upstreamRequest, webOrigin };
