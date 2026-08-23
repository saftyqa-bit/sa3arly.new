import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";

const explorerSource = await readFile(new URL("../app/price-explorer-v2.tsx", import.meta.url), "utf8");
const decisionSource = await readFile(new URL("../app/decision-panel.tsx", import.meta.url), "utf8");
const compareSource = await readFile(new URL("../app/product-compare-drawer.tsx", import.meta.url), "utf8");
const listingSource = await readFile(new URL("../app/comparison-landing.tsx", import.meta.url), "utf8");
const migrationSource = await readFile(new URL("../../db/migrations/001_sa3arly_core_v2.sql", import.meta.url), "utf8");
const reliabilityMigrationSource = migrationSource;
const liveProxySource = await readFile(new URL("../app/api/live/proxy.ts", import.meta.url), "utf8");
const liveDataSource = await readFile(new URL("../app/live-data.ts", import.meta.url), "utf8");
const buildSource = await readFile(new URL("../scripts/build-verified.sh", import.meta.url), "utf8");
const displayNamesSource = await readFile(new URL("../app/display-names.ts", import.meta.url), "utf8");
const visiblePricesMigrationSource = migrationSource;
const collectionRuntimeSource = await readFile(new URL("../../scripts/ensure_price_collection_runtime.sh", import.meta.url), "utf8");
const productionWorkflowSource = await readFile(new URL("../../.github/workflows/deploy-production.yml", import.meta.url), "utf8");
const liveStatusRouteSource = await readFile(new URL("../app/api/live/status/route.ts", import.meta.url), "utf8");

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
    response.end(JSON.stringify({
      total: 50,
      limit: Number(url.searchParams.get("limit")),
      offset: Number(url.searchParams.get("offset")),
      items: [{
        variant_id: "VAR-ABCDEF123456",
        canonical_name: "Samsung Galaxy Test 256GB",
        section: "الموبايلات والاتصالات",
        product_type: "هواتف ذكية",
        brand: "Samsung",
        model: "Galaxy Test",
        variant_name: "256GB · أسود",
        lowest_cash_price: 29999,
        lowest_final_cost: 29749,
        lowest_delivered_total: 29749,
        cash_offer_count: 3,
        installment_plan_count: 2,
        lowest_periodic_payment: 1299,
        lowest_installment_total: 33500,
        purchase_label: "أقل من متوسط 90 يومًا بـ 8%",
        price_history: {
          lowest_30d: 29749,
          lowest_90d: 29200,
          average_90d: 32300,
          highest_90d: 34000,
          sparkline: [
            { date: "2026-07-01", price: 32900 },
            { date: "2026-07-15", price: 31500 },
            { date: "2026-08-01", price: 29749 },
          ],
        },
      }, {
        variant_id: "VAR-REVIEW123456",
        canonical_name: "Xiaomi Review Price 128GB",
        section: "الموبايلات والاتصالات",
        product_type: "هواتف ذكية",
        brand: "Xiaomi",
        model: "Review Price",
        variant_name: "128GB · أزرق",
        lowest_cash_price: 12499,
        cash_offer_count: 1,
        confirmed_cash_offer_count: 0,
        review_cash_offer_count: 1,
        cash_price_review_required: true,
        installment_plan_count: 0,
        price_history: { sparkline: [] },
      }],
    }));
    return;
  }
  if (/^\/api\/v1\/products\/VAR-[A-Z0-9]{6,40}\/comparison$/.test(url.pathname)) {
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
if (!apiAddress || typeof apiAddress === "string") throw new Error("mock API port unavailable");

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("frontend port unavailable");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

const frontendPort = await reservePort();
const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`;
const logs = [];
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
frontend.stdout.on("data", (chunk) => logs.push(chunk));
frontend.stderr.on("data", (chunk) => logs.push(chunk));

const deadline = Date.now() + 30_000;
while (Date.now() < deadline) {
  if (frontend.exitCode !== null) throw new Error(`Next.js exited: ${logs.join("")}`);
  try {
    const response = await fetch(`${frontendBaseUrl}/robots.txt`);
    if (response.ok) break;
  } catch {}
  await new Promise((resolve) => setTimeout(resolve, 150));
}

after(async () => {
  frontend.kill("SIGTERM");
  if (frontend.exitCode === null) {
    await Promise.race([
      new Promise((resolve) => frontend.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 3000)),
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
  return html.match(/<link(?=[^>]*\brel=["']canonical["'])[^>]*>/i)?.[0]?.match(/\bhref=["']([^"']*)["']/i)?.[1] ?? "";
}

test("phase two homepage starts without a default product and explains the decision flow", async () => {
  const { response, html } = await render("/");
  assert.equal(response.status, 200);
  assert.equal(title(html), "سعرلي: قارن الأسعار واعرف أفضل وقت للشراء في مصر");
  assert.match(html, /اختر المنتج وشاهد أسعاره في كل المتاجر/);
  assert.match(html, /اكتب اسم المنتج أو الموديل أو SKU/);
  assert.match(html, /اختيار المنتج/);
  assert.match(html, /تحديد النسخة/);
  assert.match(html, /أسعار المتاجر/);
  assert.match(html, /لن نختار منتجًا افتراضيًا/);
  assert.doesNotMatch(html, /0 عروض|٠ عروض/);
});

test("homepage uses a compact ad-free marketplace shell", async () => {
  const { html } = await render("/");
  assert.match(html, /class="header-product-search"/);
  assert.match(html, /class="quick-category-nav"/);
  assert.match(html, /مقارنة أسعار بدون إعلانات/);
  assert.match(explorerSource, /1\. الفئة/);
  assert.match(explorerSource, /4\. الموديل/);
  assert.doesNotMatch(html, /googlesyndication|doubleclick|ad-slot|advertisement/i);
});

test("choosing a product opens all store prices directly in cheapest-first order", () => {
  assert.match(explorerSource, /chooseVariant\(product\)/);
  assert.match(explorerSource, /اعرض أسعار المتاجر/);
  assert.match(explorerSource, /بدون إضافتها للمقارنة/);
  assert.doesNotMatch(explorerSource, /sticky-compare-button/);
  assert.match(decisionSource, /compareCashOffers/);
  assert.match(decisionSource, /cashOfferSortValue/);
  assert.match(decisionSource, /أسعار المنتج في المتاجر/);
  assert.match(decisionSource, /مرتبة من الأرخص للأغلى/);
  assert.match(decisionSource, /بدون الحاجة لإضافة المنتج للمقارنة/);
  assert.match(decisionSource, /سعر يحتاج مراجعة/);
});

test("category browsing follows demand order without importance labels", () => {
  assert.match(explorerSource, /CATEGORY_PRIORITY = \[\s*"الموبايلات والاتصالات"/);
  assert.match(explorerSource, /TELECOM_TYPE_PRIORITY = \["هواتف", "أجهزة تابلت", "ساعات ذكية"/);
  assert.match(explorerSource, /className="browse-type-grid"/);
  assert.match(explorerSource, /اختَر خطوة بخطوة/);
  assert.doesNotMatch(explorerSource, /<em>الأهم<\/em>/);
  assert.match(explorerSource, /نعرض \{formatNumber\(Math\.min\(48, filteredBrowseGroups\.length\)\)\} من/);
});

test("desktop offers render a real store-first comparison table", () => {
  assert.match(decisionSource, /className="offer-comparison-table"/);
  for (const heading of ["المتجر", "السعر", "مصاريف الشحن", "الإجمالي", "التوفر", "الضمان", "آخر تحديث", "الشراء"]) {
    assert.match(decisionSource, new RegExp(`scope="col">${heading}`));
  }
  assert.match(decisionSource, /className="decision-offers smart-offer-list mobile-offer-list"/);
});

test("the price screen prioritizes a decision summary and compact expandable store rows", () => {
  assert.match(decisionSource, /أفضل سعر متاح الآن/);
  assert.match(decisionSource, /purchasableStatuses/);
  assert.match(decisionSource, /رأي سعرلي الآن/);
  assert.match(decisionSource, /فرق الأسعار/);
  assert.match(decisionSource, /CashOfferRow/);
  assert.match(decisionSource, /تفاصيل السعر والمتجر/);
  assert.match(decisionSource, /عن الأرخص المتاح/);
  assert.match(decisionSource, /تاريخ السعر/);
  assert.match(decisionSource, /أسعار تحتاج تحققًا إضافيًا/);
  assert.match(decisionSource, /القسط الشهري وحده قد يكون مضللًا/);
});

test("phase two source contains all four modes, filters, alerts and honest channels", () => {
  for (const text of ["أوفر سعر", "أضمن شراء", "أسرع توصيل", "أفضل تقسيط"]) assert.match(decisionSource, new RegExp(text));
  for (const text of ["متوفر الآن فقط", "ضمان رسمي", "استلام من الفرع"]) assert.match(decisionSource, new RegExp(text));
  assert.match(decisionSource, /at_90_day_low/);
  assert.match(decisionSource, /interest_free_installment/);
  assert.match(decisionSource, /coupon_available/);
  assert.match(decisionSource, /awaiting_provider|ربط مزود القناة/);
  assert.match(decisionSource, /بلّغ عن سعر خاطئ/);
});

test("true-cost formulas and anomaly protection are schema contracts", () => {
  assert.match(migrationSource, /mandatory_fees/);
  assert.match(migrationSource, /coupon_discount/);
  assert.match(migrationSource, /final_cost/);
  assert.match(migrationSource, /final_installment_cost/);
  assert.match(migrationSource, /extreme_market_outlier/);
  assert.match(migrationSource, /weak_variant_match/);
  assert.match(migrationSource, /installment_total_inconsistent/);
  assert.match(migrationSource, /store_quality_metrics/);
});

test("catalog delivery, Cloud Run authentication and price reliability are enforced", () => {
  assert.match(buildSource, /public\/catalog-data\.json/);
  assert.match(liveProxySource, /cloudRunAuthorizationHeaders\(base\)/);
  assert.match(reliabilityMigrationSource, /below_public_price_floor/);
  assert.match(reliabilityMigrationSource, /cash_price < 10/);
  assert.match(liveDataSource, /validPublicCashPrice/);
  assert.match(liveDataSource, /const seen = new Set<string>/);
  assert.match(explorerSource, /setBrowseCategory\(category\)/);
  assert.match(explorerSource, /setBrowseBrand\(brand\)/);
  assert.match(compareSource, /setOpen\(true\)/);
  assert.match(displayNamesSource, /stripLeadingWords/);
  assert.match(displayNamesSource, /collapseRepeatedName/);
});

test("review prices remain visible without becoming recommendations and collection restarts", () => {
  assert.match(visiblePricesMigrationSource, /anomaly_status <> 'blocked'/);
  assert.match(visiblePricesMigrationSource, /anomaly_status = 'clear'/);
  assert.match(collectionRuntimeSource, /gcloud tasks queues resume/);
  assert.match(collectionRuntimeSource, /gcloud scheduler jobs run/);
  assert.match(productionWorkflowSource, /ensure_price_collection_runtime\.sh/);
  assert.match(productionWorkflowSource, /\[force-deploy\]/);
  assert.match(liveStatusRouteSource, /\/api\/v1\/status/);
});

test("cash listing page two uses live pagination, known delivered cost and sparkline", async () => {
  pricedRequests.length = 0;
  const { response, html } = await render("/cash?page=2");
  assert.equal(response.status, 200);
  assert.equal(canonical(html), "https://sa3arly.com/cash");
  assert.match(html, /Samsung Galaxy Test 256GB/);
  assert.match(html, /التكلفة بعد الشحن من/);
  assert.match(html, /Xiaomi Review Price 128GB/);
  assert.match(html, /سعر مرصود — يحتاج مراجعة/);
  assert.match(html, /ظاهر للشفافية — لا يدخل في التوصيات/);
  assert.match(html, /أقل من متوسط 90 يومًا بـ 8%/);
  assert.match(html, /listing-sparkline/);
  assert.match(html, /أضف للمقارنة/);
  assert.equal(pricedRequests[0].searchParams.get("limit"), "24");
  assert.equal(pricedRequests[0].searchParams.get("offset"), "24");
  assert.match(listingSource, /lowestFinalCost/);
  assert.match(listingSource, /lowestInstallmentTotal/);
});

test("Arabic search, recent products and browser-back state have explicit contracts", () => {
  assert.match(explorerSource, /arabicDigits/);
  assert.match(explorerSource, /سامسونج ايه ٥٥/);
  assert.match(explorerSource, /manufacturer_sku|SKU/);
  assert.match(explorerSource, /sa3arly-last-query/);
  assert.match(explorerSource, /history\.replaceState/);
  assert.match(explorerSource, /sa3arly-recent-products/);
  assert.match(explorerSource, /sa3arly-compare-products/);
  assert.match(explorerSource, /catalogRef\.current/);
});

test("four-product comparison supports differences-only and image sharing", () => {
  assert.match(compareSource, /selectedIds\.length < 2/);
  assert.match(compareSource, /current\.length >= MAX_COMPARE|4 - selected\.length/);
  assert.match(compareSource, /أظهر الاختلافات فقط/);
  assert.match(compareSource, /canvas\.toBlob/);
  assert.match(compareSource, /navigator\.share/);
  assert.match(compareSource, /sa3arly-comparison\.png/);
});
