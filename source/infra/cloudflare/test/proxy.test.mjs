import assert from "node:assert/strict";
import test from "node:test";

import worker, {
  publicLocation,
  upstreamRequest,
  webOrigin,
} from "../src/index.mjs";

const origin = new URL("https://sa3arly-web.example.run.app");

test("forwards the full public path and query to Cloud Run", () => {
  const request = new Request(
    "https://www.sa3arly.com/products/iphone-17?sort=price",
    { headers: { accept: "text/html" } },
  );
  const upstream = upstreamRequest(request, origin);

  assert.equal(
    upstream.url,
    "https://sa3arly-web.example.run.app/products/iphone-17?sort=price",
  );
  assert.equal(upstream.headers.get("x-forwarded-host"), "www.sa3arly.com");
  assert.equal(upstream.headers.get("x-forwarded-proto"), "https");
});

test("rewrites an origin redirect back to the public domain", () => {
  assert.equal(
    publicLocation(
      "https://sa3arly-web.example.run.app/cash",
      origin,
      new URL("https://sa3arly.com/"),
    ),
    "https://sa3arly.com/cash",
  );
});

test("leaves external redirects unchanged", () => {
  assert.equal(
    publicLocation(
      "https://merchant.example/product/1",
      origin,
      new URL("https://sa3arly.com/"),
    ),
    "https://merchant.example/product/1",
  );
});

test("rejects a non-HTTPS origin", () => {
  assert.throws(
    () => webOrigin({ SA3ARLY_WEB_ORIGIN: "http://example.test" }),
    /must use HTTPS/,
  );
});

test("adds the deployment marker to upstream responses", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (request) => {
    assert.equal(new URL(request.url).hostname, "sa3arly-web.example.run.app");
    return new Response("ok", { status: 200 });
  };

  try {
    const response = await worker.fetch(
      new Request("https://www.sa3arly.com/"),
      { SA3ARLY_WEB_ORIGIN: origin.toString() },
    );

    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-sa3arly-edge"), "cloudflare-web-proxy-v1");
    assert.equal(await response.text(), "ok");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
