import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";

const priceExplorerSource = await readFile(
  new URL("../app/price-explorer.tsx", import.meta.url),
  "utf8",
);
const comparisonLandingSource = await readFile(
  new URL("../app/comparison-landing.tsx", import.meta.url),
  "utf8",
);
const liveDataSource = await readFile(
  new URL("../app/live-data.ts", import.meta.url),
  "utf8",
);
const catalogSelectorSource = await readFile(
  new URL("../app/catalog-selectors.ts", import.meta.url),
  "utf8",
);

const pricedRequests = [];
const apiServer = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  response.setHeader("content-type", "application/json; charset=utf-8");

  if (url.pathname === "/api/v1/status") {
    response.statusCode = 503;
    response.end(JSON.stringify({ detail: "test price engine unavailable" }));
    return;
  }

  if (url.pathname === "/api/v1/products/priced") {
    pricedRequests.push(url);
    const mode = url.searchParams.get("mode");
    response.statusCode = 200;
    response.end(
      JSON.stringify({
        total: 50,
        limit: Number(url.searchParams.get("limit")),
        offset: Number(url.searchParams.get("offset")),
        items: [
          {
            variant_id: "VAR-ABCDEF123456",
            canonical_name: "Samsung Galaxy Test 256GB",
            section: "الموبايلات والاتصالات",
            product_type: "هواتف ذكية",
            brand: "Samsung",
            model: "Galaxy Test",
            variant_name: "256GB · أسود",
            lowest_cash_price: mode === "cash" ? 29999 : null,
            lowest_delivered_total: mode === "cash" ? 30149 : null,
            cash_offer_count: mode === "cash" ? 3 : 0,
            installment_plan_count: mode === "installment" ? 2 : 0,
            lowest_periodic_payment: mode === "installment" ? 1299 : null,
          },
        ],
      }),
    );
    return;
  }

  if (/^\/api\/v1\/products\/VAR-[A-Z0-9]{6,40}\/comparison$/.test(url.pathname)) {
    response.statusCode = 200;
    response.end(JSON.stringify({ cash_offers: [], installment_plans: [] }));
    return;
  }

  response.statusCode = 404;
  response.end(JSON.stringify({ detail: "not found" }));
});

await new Promise((resolve, reject) => {
  apiServer.once("error", reject);
  apiServer.listen(0, "127.0.0.1", resolve);
});
const apiAddress = apiServer.address();
if (!apiAddress || typeof apiAddress === "string") {
  throw new Error("mock API did not receive a TCP port");
}

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("could not reserve a TCP port");
  }
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

const frontendPort = await reservePort();
const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`;
const frontendLogs = [];
const frontend = spawn(process.execPath, ["server.js"], {
  cwd: new URL("../.next/standalone/", import.meta.url),
  env: {
    ...process.env,
    NODE_ENV: "production",
    HOSTNAME: "127.0.0.1",
    PORT: String(frontendPort),
    SA3ARLY_API_BASE_URL: `http://127.0.0.1:${apiAddress.port}`,
  },
  stdio: ["ignore", "pipe", "pipe"],
});
frontend.stdout.setEncoding("utf8");
frontend.stderr.setEncoding("utf8");
frontend.stdout.on("data", (chunk) => frontendLogs.push(chunk));
frontend.stderr.on("data", (chunk) => frontendLogs.push(chunk));

async function waitForFrontend() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (frontend.exitCode !== null) {
      throw new Error(
        `Next.js exited with ${frontend.exitCode}: ${frontendLogs.join("")}`,
      );
    }
    try {
      const response = await fetch(`${frontendBaseUrl}/robots.txt`);
      if (response.status === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Next.js did not become ready: ${frontendLogs.join("")}`);
}
await waitForFrontend();

after(async () => {
  frontend.kill("SIGTERM");
  if (frontend.exitCode === null) {
    await Promise.race([
      new Promise((resolve) => frontend.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 3_000)),
    ]);
  }
  if (frontend.exitCode === null) frontend.kill("SIGKILL");
  await new Promise((resolve) => apiServer.close(resolve));
});

async function render(path, headers = {}) {
  const response = await fetch(`${frontendBaseUrl}${path}`, {
    headers: { accept: "text/html", ...headers },
    redirect: "manual",
  });
  return { response, html: await response.text() };
}

function title(html) {
  return html.match(/<title>([^<]*)<\/title>/i)?.[1] ?? "";
}

function canonical(html) {
  return (
    html
      .match(/<link(?=[^>]*\brel=["']canonical["'])[^>]*>/i)?.[0]
      ?.match(/\bhref=["']([^"']*)["']/i)?.[1] ?? ""
  );
}

test("S1-S3 homepage starts empty and presents the three-step smart flow", async () => {
  const { response, html } = await render("/");
  assert.equal(response.status, 200);
  assert.equal(title(html), "سعرلي: قارن أسعار المنتجات والكاش والتقسيط في مصر");
  assert.match(html, /<h1[^>]*>دور على المنتج اللي عايز تقارن سعره<\/h1>/);
  assert.match(html, /اكتب اسم المنتج أو الموديل/);
  assert.match(html, /تصفح حسب الفئة/);
  assert.match(html, /اختيار المنتج/);
  assert.match(html, /تحديد النسخة/);
  assert.match(html, /مقارنة الأسعار/);
  assert.match(html, /ابدأ بالبحث عن منتج/);
  assert.doesNotMatch(html, /لم نحدد المنتج|0 عروض|٠ عروض/);
  assert.doesNotMatch(html, /iPhone 17 Pro Max/);
});

test("S5 and S8 hide technical status and refresh timing until useful", async () => {
  const { html } = await render("/");
  assert.doesNotMatch(html, /المحرك متصل|جاري فحص الأسعار/);
  assert.doesNotMatch(html, /يتم تحديث الأسعار\s*مرتين يوميًا/);
  assert.doesNotMatch(html, /لم يبدأ الرصد الحي بعد/);
  assert.match(priceExplorerSource, /latestUpdate &&/);
  assert.match(priceExplorerSource, /آخر تحديث ناجح/);
  assert.match(priceExplorerSource, /تحديث الأسعار متأخر مؤقتًا/);
});

test("S2 keeps the large catalog out of the initial client bundle contract", () => {
  assert.match(priceExplorerSource, /fetch\(CATALOG_URL\)/);
  assert.match(priceExplorerSource, /const CATALOG_URL = "\/catalog-data\.json"/);
  assert.doesNotMatch(priceExplorerSource, /import catalog from/);
  assert.match(catalogSelectorSource, /import type CatalogDataShape/);
});

test("S7 mobile and desktop receive the same primary server-rendered flow", async () => {
  const mobile = await render("/", {
    "user-agent":
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
  });
  const desktop = await render("/", {
    "user-agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
  });
  for (const html of [mobile.html, desktop.html]) {
    assert.match(html, /دور على المنتج اللي عايز تقارن سعره/);
    assert.match(html, /اكتب اسم المنتج أو الموديل/);
    assert.match(html, /اختيار المنتج/);
    assert.match(html, /تحديد النسخة/);
    assert.match(html, /مقارنة الأسعار/);
    assert.match(html, /ابدأ بالبحث عن منتج/);
  }
});

test("cash page 2 uses live limit and offset instead of a static 24-item slice", async () => {
  pricedRequests.length = 0;
  const { response, html } = await render("/cash?page=2");
  assert.equal(response.status, 200);
  assert.equal(canonical(html), "https://sa3arly.com/cash");
  assert.match(html, /Samsung Galaxy Test 256GB/);
  assert.match(html, /الصفحة السابقة/);
  assert.match(html, /الصفحة التالية/);
  assert.equal(pricedRequests.length, 1);
  assert.equal(pricedRequests[0].searchParams.get("mode"), "cash");
  assert.equal(pricedRequests[0].searchParams.get("limit"), "24");
  assert.equal(pricedRequests[0].searchParams.get("offset"), "24");
  assert.match(comparisonLandingSource, /const PAGE_SIZE = 24/);
  assert.match(comparisonLandingSource, /offset: \(safePage - 1\) \* PAGE_SIZE/);
  assert.doesNotMatch(comparisonLandingSource, /\.slice\(0, ?24\)/);
  assert.match(
    liveDataSource,
    /\/api\/v1\/products\/priced\?mode=\$\{mode\}&limit=\$\{limit\}&offset=\$\{offset\}/,
  );
});

test("S4 and S6 keep empty-state actions and media claims honest", () => {
  assert.match(priceExplorerSource, /لم نجد سعرًا حيًا لهذه النسخة الآن/);
  assert.match(priceExplorerSource, /آخر سعر موثّق/);
  assert.match(priceExplorerSource, /متاجر تبيع نفس الموديل/);
  assert.match(priceExplorerSource, /احفظ النسخة للتنبيه/);
  assert.match(priceExplorerSource, /الإرسال التلقائي سيبدأ بعد ربط خدمة الإشعارات/);
  assert.match(priceExplorerSource, /image_url/);
  assert.match(priceExplorerSource, /store_logo_url/);
  assert.match(priceExplorerSource, /product-image-fallback/);
  assert.match(priceExplorerSource, /store-logo-fallback/);
  assert.match(priceExplorerSource, /verified-offer/);
});
