import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";

const catalog = JSON.parse(
  await readFile(new URL("../app/catalog-data.json", import.meta.url), "utf8"),
);
const priceExplorerSource = await readFile(
  new URL("../app/price-explorer.tsx", import.meta.url),
  "utf8",
);

let liveComparison = null;
const apiServer = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  response.setHeader("content-type", "application/json; charset=utf-8");
  if (url.pathname === "/api/v1/status") {
    response.statusCode = 503;
    response.end(JSON.stringify({ detail: "test price engine unavailable" }));
    return;
  }
  if (/^\/api\/v1\/products\/VAR-[A-Z0-9]{6,40}\/comparison$/.test(url.pathname)) {
    response.statusCode = 200;
    response.end(
      JSON.stringify(
        liveComparison ?? { cash_offers: [], installment_plans: [] },
      ),
    );
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

function slugify(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .trim()
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}

function productPath(product) {
  return `/products/${slugify(product.brand)}-${slugify(product.model)}-${product.id.toLowerCase()}`;
}

async function request(path, headers = {}) {
  return fetch(`${frontendBaseUrl}${path}`, {
    headers: { accept: "text/html", ...headers },
    redirect: "manual",
  });
}

async function render(path, headers = {}) {
  const response = await request(path, headers);
  return { response, html: await response.text() };
}

function metaContent(html, name) {
  const tag =
    html.match(
      new RegExp(
        `<meta(?=[^>]*\\bname=["']${name}["'])(?=[^>]*\\bcontent=["'][^"']*["'])[^>]*>`,
        "i",
      ),
    )?.[0] ?? "";
  return tag.match(/\bcontent=["']([^"']*)["']/i)?.[1] ?? "";
}

function canonical(html) {
  return (
    html
      .match(/<link(?=[^>]*\brel=["']canonical["'])[^>]*>/i)?.[0]
      ?.match(/\bhref=["']([^"']*)["']/i)?.[1] ?? ""
  );
}

function title(html) {
  return html.match(/<title>([^<]*)<\/title>/i)?.[1] ?? "";
}

function jsonLd(html) {
  return [...html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>(.*?)<\/script>/gis)]
    .map((match) => JSON.parse(match[1]))
    .filter(Boolean);
}

function sitemapLocations(xml) {
  return [...xml.matchAll(/<loc>(.*?)<\/loc>/g)].map((match) =>
    match[1].replaceAll("&amp;", "&"),
  );
}

const presence = catalog.presence;
const evidenceTokenStoplist = new Set([
  "gb", "ram", "storage", "w", "kg", "eg", "en", "p", "5g", "4g",
  "lte", "dual", "sim", "كجم", "لتر", "بوصة", "وات", "سم", "مل",
]);
const evidenceSpecification =
  /اللون|السعة|ram|الذاكرة|الحمولة|مقاس|الحجم/i;
function evidenceTokens(value) {
  let text = String(value ?? "");
  try {
    text = decodeURIComponent(text);
  } catch {}
  return text
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(/\s+/)
    .map((token) =>
      token.replace(
        /^(\d+(?:\.\d+)?)(?:gb|tb|mb|kg|g|l|ml|w|inch|inches|hz|cm|mm)$/i,
        "$1",
      ),
    )
    .filter(
      (token) =>
        token &&
        (token.length > 1 || /^\d$/.test(token)) &&
        !evidenceTokenStoplist.has(token),
    );
}
function sourceContainsValue(sourceTokens, value) {
  const expected = evidenceTokens(value);
  return expected.length > 0 && expected.every((token) => sourceTokens.has(token));
}
function isVerifiedPresence(product, item) {
  if (
    item.matchConfidence !== "عالية" ||
    item.reviewStatus !== "جاهز لأول رصد"
  ) {
    return false;
  }
  if (item.linkType === "تغذية منتجات") return true;
  if (item.linkType !== "رابط منتج مباشر") return false;
  const sourceTokens = new Set(evidenceTokens(item.sourceUrl));
  return (
    sourceContainsValue(sourceTokens, product.brand) &&
    sourceContainsValue(sourceTokens, product.model) &&
    product.specs
      .filter((specification) =>
        evidenceSpecification.test(specification.name),
      )
      .every((specification) =>
        sourceContainsValue(sourceTokens, specification.value),
      )
  );
}
const indexableProducts = catalog.products.filter(
  (product) =>
    product.brand &&
    product.model &&
    product.type &&
    Array.isArray(presence[product.id]) &&
    presence[product.id].some((item) => isVerifiedPresence(product, item)),
);
const awaitingProduct = catalog.products.find(
  (product) =>
    !presence[product.id]?.some((item) => isVerifiedPresence(product, item)),
);
const selectedProduct =
  catalog.products.find((product) => product.id === "VAR-2284D7BFDA60C0") ??
  indexableProducts[0];

test("homepage renders the exact requested metadata and visible H1", async () => {
  const { response, html } = await render("/");
  assert.equal(response.status, 200);
  assert.equal(
    title(html),
    "سعرلي: قارن أسعار المنتجات والكاش والتقسيط في مصر",
  );
  assert.equal(
    metaContent(html, "description"),
    "قارن أسعار الموبايلات والإلكترونيات والأجهزة والمنتجات العامة بين متاجر مصر، واعرض خيارات الكاش والتقسيط والعروض قبل الشراء.",
  );
  assert.equal(new URL(canonical(html)).href, "https://sa3arly.com/");
  assert.match(metaContent(html, "robots"), /index/i);
  assert.match(metaContent(html, "robots"), /max-image-preview:large/i);
  assert.match(html, /<html[^>]*lang=["']ar["'][^>]*dir=["']rtl["']/i);
  assert.match(html, /<h1[^>]*>قارن أسعار المنتجات والكاش والتقسيط في مصر<\/h1>/);
  assert.doesNotMatch(html, /name=["']keywords["']/i);
  assert.doesNotMatch(html, /PRODUCTION_SITE_URL|YOUR-|PLACEHOLDER/i);
});

test("homepage quietly states the twice-daily refresh without exposing freshness thresholds", async () => {
  const { html } = await render("/");
  assert.match(html, /يتم تحديث الأسعار\s*مرتين يوميًا/);
  assert.doesNotMatch(
    priceExplorerSource,
    /رصد حديث|التحديث متأخر قليلًا|14 ساعة|28 ساعة|855|1710/,
  );
});

test("homepage exposes Organization and WebSite structured data", async () => {
  const { html } = await render("/");
  const schemas = jsonLd(html);
  assert.ok(schemas.some((schema) => schema["@type"] === "Organization"));
  assert.ok(schemas.some((schema) => schema["@type"] === "WebSite"));
});

test("filter and legacy product query combinations are noindex with clean canonical", async () => {
  for (const path of ["/?brand=Apple", "/?sort=price", "/?product=VAR-TEST"]) {
    const { response, html } = await render(path);
    assert.equal(response.status, 200);
    assert.match(metaContent(html, "robots"), /noindex/i);
    assert.equal(new URL(canonical(html)).href, "https://sa3arly.com/");
  }
});

test("the eight main categories use the requested /c routes and crawlable metadata", async () => {
  const cases = [
    [
      "/c/mobiles",
      "أسعار الموبايلات والاتصالات في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الموبايلات والاتصالات",
    ],
    [
      "/c/home-appliances",
      "أسعار الأجهزة المنزلية في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الأجهزة المنزلية",
    ],
    [
      "/c/kitchen",
      "أسعار المطبخ والأدوات المنزلية في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات المطبخ والأدوات المنزلية",
    ],
    [
      "/c/tv-audio",
      "أسعار التلفزيونات والصوتيات والتصوير في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات التلفزيونات والصوتيات والتصوير",
    ],
    [
      "/c/computers",
      "أسعار الكمبيوتر والشبكات في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الكمبيوتر والشبكات",
    ],
    [
      "/c/gaming",
      "أسعار الألعاب الإلكترونية في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الألعاب الإلكترونية",
    ],
    [
      "/c/toys-hobbies",
      "أسعار الألعاب والهوايات في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الألعاب والهوايات",
    ],
    [
      "/c/baby",
      "أسعار الأطفال والأمومة في مصر ومقارنة المتاجر | سعرلي",
      "قارن أسعار ومواصفات الأطفال والأمومة",
    ],
  ];
  for (const [path, expectedTitle, expectedDescription] of cases) {
    const { response, html } = await render(path);
    assert.equal(response.status, 200, path);
    assert.equal(title(html), expectedTitle);
    assert.match(metaContent(html, "description"), new RegExp(expectedDescription));
    assert.match(html, /href=["']\/products\//);
    assert.match(html, /BreadcrumbList/);
    assert.doesNotMatch(metaContent(html, "robots"), /noindex/i);
  }
});

test("the approved category order, totals, and hierarchy are rendered", async () => {
  const directory = await render("/categories");
  const orderedCategories = [
    ["الموبايلات والاتصالات", "482"],
    ["الأجهزة المنزلية", "477"],
    ["المطبخ والأدوات المنزلية", "891"],
    ["التلفزيونات والصوتيات والتصوير", "262"],
    ["الكمبيوتر والشبكات", "133"],
    ["الألعاب الإلكترونية", "121"],
    ["الألعاب والهوايات", "60"],
    ["الأطفال والأمومة", "45"],
  ];
  let previousIndex = -1;
  for (const [name, total] of orderedCategories) {
    const index = directory.html.indexOf(name, previousIndex + 1);
    assert.ok(index > previousIndex, name);
    assert.match(directory.html.slice(index, index + 350), new RegExp(total));
    previousIndex = index;
  }

  const kitchen = await render("/c/kitchen");
  assert.match(kitchen.html, /891(?:<!-- -->)? منتجًا/);
  let previousGroupIndex = -1;
  for (const group of [
    "حلل وأواني الطهي",
    "التقديم والسفرة",
    "حفظ وتخزين الطعام",
    "أكواب وزجاجات وأباريق",
    "أدوات المطبخ والتحضير",
    "أدوات التنظيف والقمامة",
    "منسوجات المطبخ",
    "السكاكين وأدوات التقطيع",
    "أدوات القهوة والشاي",
  ]) {
    const index = kitchen.html.indexOf(group, previousGroupIndex + 1);
    assert.ok(index > previousGroupIndex, group);
    previousGroupIndex = index;
  }
  assert.match(kitchen.html, /حلل مكرونة ومتعددة الاستخدام/);
  assert.match(kitchen.html, /موزعات صابون/);

  const tvAudio = await render("/c/tv-audio");
  assert.match(tvAudio.html, /262(?:<!-- -->)? منتجًا/);
  assert.match(tvAudio.html, /تليفزيونات وأجهزة عرض/);
  assert.match(tvAudio.html, /الكاميرات والتصوير/);
});

test("category hierarchy adds ranked brands, model totals, and dynamic specification dimensions", async () => {
  const mobiles = await render("/c/mobiles");
  assert.match(mobiles.html, /هواتف ذكية/);
  assert.match(
    mobiles.html,
    /307(?:<!-- -->)? نسخة [·/][\s\S]{0,30}?94(?:<!-- -->)? موديل/,
  );

  const hierarchyStart = mobiles.html.indexOf("الماركات والموديلات والمواصفات داخل كل نوع");
  const phoneStart = mobiles.html.indexOf("هواتف ذكية", hierarchyStart);
  const phoneEnd = mobiles.html.indexOf("أجهزة تابلت", phoneStart + 1);
  const phoneHierarchy = mobiles.html.slice(phoneStart, phoneEnd);
  const rankedBrands = ["Xiaomi", "Apple", "Samsung", "Honor", "Infinix"];
  let previousBrandIndex = -1;
  for (const brand of rankedBrands) {
    const index = phoneHierarchy.indexOf(brand, previousBrandIndex + 1);
    assert.ok(index > previousBrandIndex, brand);
    previousBrandIndex = index;
  }
  assert.match(phoneHierarchy, /Xiaomi/);
  assert.match(phoneHierarchy, /63(?:<!-- -->)? نسخة/);
  assert.match(phoneHierarchy, /الذاكرة \(RAM\)/);
  assert.match(phoneHierarchy, /السعة التخزينية/);
  assert.match(phoneHierarchy, /اللون/);

  const home = await render("/");
  assert.match(home.html, /هواتف ذكية \(307 نسخة \/ 94 موديل\)/);
  assert.match(home.html, /Xiaomi \(63\)/);
});

test("legacy main-category routes permanently redirect to the new canonicals", async () => {
  for (const [legacy, canonicalPath] of [
    ["/categories/mobiles", "/c/mobiles"],
    ["/categories/home-appliances", "/c/home-appliances"],
  ]) {
    const response = await request(legacy);
    assert.equal(response.status, 308, legacy);
    assert.equal(
      new URL(response.headers.get("location"), "http://localhost").pathname,
      canonicalPath,
    );
  }
});

test("specific product-type pages remain available below the main categories", async () => {
  for (const path of [
    "/categories/laptops",
    `/categories/category-${encodeURIComponent(slugify("تلفزيونات"))}`,
  ]) {
    const { response, html } = await render(path);
    assert.equal(response.status, 200, path);
    assert.match(html, /href=["']\/products\//);
    assert.doesNotMatch(metaContent(html, "robots"), /noindex/i);
  }
});

test("twenty distinct product pages render public initial HTML and truthful no-price state", async () => {
  const samples = [];
  const seenTypes = new Set();
  for (const product of indexableProducts) {
    if (!seenTypes.has(product.type)) {
      samples.push(product);
      seenTypes.add(product.type);
    }
    if (samples.length === 20) break;
  }
  assert.equal(samples.length, 20);
  for (const product of samples) {
    const path = productPath(product);
    const { response, html } = await render(path);
    assert.equal(response.status, 200, path);
    assert.match(html, new RegExp(product.model.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.match(html, /وقت آخر تحديث/);
    assert.match(html, /لم يُستدل على السعر/);
    assert.match(html, /BreadcrumbList/);
    assert.match(html, /"@type":"Product"/);
    assert.match(html, /<img[^>]*\/image/);
    assert.doesNotMatch(html, /تسجيل الدخول|Login redirect/i);
    assert.equal(canonical(html), `https://sa3arly.com${path}`);
  }
});

test("mapped no-price product has Product schema without Offer or AggregateOffer", async () => {
  delete process.env.SA3ARLY_API_BASE_URL;
  const { html } = await render(productPath(selectedProduct));
  const productSchema = jsonLd(html).find(
    (schema) => schema["@type"] === "Product",
  );
  assert.ok(productSchema);
  assert.equal("offers" in productSchema, false);
  assert.match(html, /لم يُستدل على السعر/);
  assert.doesNotMatch(html, /"price":0/);
});

async function withMockLiveComparison(comparison, action) {
  const previous = liveComparison;
  liveComparison = comparison;
  try {
    return await action();
  } finally {
    liveComparison = previous;
  }
}

test("one visible EGP price produces one Offer with matching displayed value", async () => {
  await withMockLiveComparison(
    {
      cash_offers: [
        {
          offer_id: "TEST-ONE",
          store_name: "Test Store",
          cash_price: 29999,
          currency: "EGP",
          availability: "available",
          source_url: "https://example.com/product",
          last_success_at: "2026-07-31T05:05:36Z",
        },
      ],
      installment_plans: [],
    },
    async () => {
      const { html } = await render(productPath(selectedProduct));
      const schema = jsonLd(html).find((item) => item["@type"] === "Product");
      assert.equal(schema.offers["@type"], "Offer");
      assert.equal(schema.offers.price, 29999);
      assert.equal(schema.offers.priceCurrency, "EGP");
      assert.match(html, /Test Store/);
      assert.match(html, /29/);
    },
  );
});

test("multiple visible EGP prices produce a matching AggregateOffer", async () => {
  await withMockLiveComparison(
    {
      cash_offers: [
        {
          offer_id: "TEST-LOW",
          store_name: "Store A",
          cash_price: 28999,
          currency: "EGP",
          source_url: "https://example.com/a",
        },
        {
          offer_id: "TEST-HIGH",
          store_name: "Store B",
          cash_price: 30999,
          currency: "EGP",
          source_url: "https://example.com/b",
        },
      ],
      installment_plans: [],
    },
    async () => {
      const { html } = await render(productPath(selectedProduct));
      const schema = jsonLd(html).find((item) => item["@type"] === "Product");
      assert.equal(schema.offers["@type"], "AggregateOffer");
      assert.equal(schema.offers.priceCurrency, "EGP");
      assert.equal(schema.offers.lowPrice, 28999);
      assert.equal(schema.offers.highPrice, 30999);
      assert.equal(schema.offers.offerCount, 2);
      assert.equal(schema.offers.offers.length, 2);
      assert.match(html, /Store A/);
      assert.match(html, /Store B/);
    },
  );
});

test("cash and installment landing pages use the requested metadata and definitions", async () => {
  const cash = await render("/cash");
  assert.equal(
    title(cash.html),
    "مقارنة أسعار الكاش للمنتجات في متاجر مصر | سعرلي",
  );
  assert.match(cash.html, /السعر النقدي/);
  assert.match(cash.html, /السعر غير متاح/);
  assert.equal(canonical(cash.html), "https://sa3arly.com/cash");

  const installments = await render("/installments");
  assert.equal(
    title(installments.html),
    "مقارنة تقسيط المنتجات والدفعات في مصر | سعرلي",
  );
  for (const term of [
    "المقدم",
    "القسط الشهري",
    "عدد الشهور",
    "إجمالي المدفوع",
    "الرسوم الإدارية",
    "شروط الأهلية",
  ]) {
    assert.match(installments.html, new RegExp(term));
  }
  assert.match(installments.html, /لا نستخدم «بدون فوائد» إلا عندما تؤكد البيانات ذلك/);
});

test("brand and store pages contain real product links and self-canonicals", async () => {
  const apple = await render("/brands/apple");
  assert.equal(apple.response.status, 200);
  assert.equal(
    title(apple.html),
    "أسعار منتجات Apple في مصر ومقارنة المتاجر | سعرلي",
  );
  assert.match(apple.html, /href=["']\/products\//);
  assert.equal(canonical(apple.html), "https://sa3arly.com/brands/apple");

  const stores = await render("/stores");
  const storeLink = stores.html.match(/href=["'](\/stores\/[^"']+)["']/)?.[1];
  assert.ok(storeLink);
  const store = await render(storeLink);
  assert.equal(store.response.status, 200);
  assert.match(store.html, /لا ندّعي وجود شراكة رسمية/);
  assert.match(store.html, /href=["']\/products\//);
  assert.equal(canonical(store.html), `https://sa3arly.com${storeLink}`);
});

test("awaiting-mapping product remains noindex and is excluded from product sitemap", async () => {
  assert.ok(awaitingProduct);
  const path = productPath(awaitingProduct);
  const page = await render(path);
  assert.equal(page.response.status, 200);
  assert.match(metaContent(page.html, "robots"), /noindex/i);

  const sitemap = await render("/sitemap-products.xml");
  assert.doesNotMatch(sitemap.html, new RegExp(awaitingProduct.id.toLowerCase()));
});

test("sitemap index and child maps contain only canonical public pages with real lastmod", async () => {
  const sitemapIndex = await render("/sitemap.xml");
  assert.equal(sitemapIndex.response.status, 200);
  for (const file of [
    "sitemap-products.xml",
    "sitemap-categories.xml",
    "sitemap-brands.xml",
    "sitemap-stores.xml",
  ]) {
    assert.match(sitemapIndex.html, new RegExp(`https://sa3arly\\.com/${file}`));
  }

  const products = await render("/sitemap-products.xml");
  assert.equal(sitemapLocations(products.html).length, indexableProducts.length);
  assert.equal(indexableProducts.length, 567);
  const normalizedLastmod = new Date(catalog.generatedAt).toISOString();
  assert.match(
    products.html,
    new RegExp(normalizedLastmod.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
  );
  assert.doesNotMatch(products.html, /admin|login|api\/|[?&](brand|sort|price|page)=/i);

  const categories = await render("/sitemap-categories.xml");
  const brands = await render("/sitemap-brands.xml");
  const stores = await render("/sitemap-stores.xml");
  assert.equal(sitemapLocations(categories.html).length, 53);
  assert.equal(sitemapLocations(brands.html).length, 51);
  assert.equal(sitemapLocations(stores.html).length, 8);
});

test("robots.txt allows public pages, protects private surfaces, and declares all sitemaps", async () => {
  const { response, html } = await render("/robots.txt");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/plain/i);
  assert.match(html, /Allow: \//);
  assert.match(html, /Disallow: \/api\//);
  assert.match(html, /Disallow: \/admin\//);
  assert.match(html, /Sitemap: https:\/\/sa3arly\.com\/sitemap\.xml/);
  assert.match(html, /Sitemap: https:\/\/sa3arly\.com\/sitemap-products\.xml/);
});

test("API routes return noindex headers and do not affect public catalog rendering", async () => {
  const response = await request("/api/live/status", { accept: "application/json" });
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("x-robots-tag"), "noindex, nofollow");
  const home = await render("/");
  assert.equal(home.response.status, 200);
  assert.match(home.html, /458 مصدرًا محفوظًا/);
  assert.match(home.html, /2,471|٢٬٤٧١|2471/);
});

test("mobile and desktop user agents receive the same primary server-rendered content", async () => {
  const mobile = await render("/", {
    "user-agent":
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
  });
  const desktop = await render("/", {
    "user-agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
  });
  for (const html of [mobile.html, desktop.html]) {
    assert.match(html, /قارن أسعار المنتجات والكاش والتقسيط في مصر/);
    assert.match(html, />النوع</);
    for (const category of [
      "الموبايلات والاتصالات",
      "الأجهزة المنزلية",
      "المطبخ والأدوات المنزلية",
      "التلفزيونات والصوتيات والتصوير",
      "الكمبيوتر والشبكات",
      "الألعاب الإلكترونية",
      "الألعاب والهوايات",
      "الأطفال والأمومة",
    ]) {
      assert.match(html, new RegExp(category));
    }
    assert.match(html, /iPhone 17 Pro Max/);
  }
});

test("database, mappings, authentication-free comparison, and analytics stay intact", async () => {
  assert.equal(catalog.stats.products, 2471);
  assert.equal(catalog.stats.registryStores, 458);
  assert.equal(catalog.stats.selectedSectorStores, 226);
  assert.equal(catalog.stats.selectedProductTypes, 609);
  assert.equal(catalog.stats.readyProductTypes, 130);
  assert.equal(catalog.stats.mappings, 2360);
  assert.equal(catalog.sources.length, 458);

  const home = await render("/");
  assert.match(home.html, /G-030V4E9EWT/);
  assert.match(home.html, /الكاش/);
  assert.match(home.html, /التقسيط/);
  assert.doesNotMatch(home.html, /required.*login|تسجيل الدخول مطلوب/i);
});

test("same-domain Open Graph and product illustration assets are valid", async () => {
  const home = await render("/");
  assert.match(home.html, /https:\/\/sa3arly\.com\/og-image\.png/);
  const image = await request(`${productPath(selectedProduct)}/image`, {
    accept: "image/svg+xml",
  });
  assert.equal(image.status, 200);
  assert.match(image.headers.get("content-type") ?? "", /^image\/svg\+xml/i);
  assert.match(await image.text(), /بطاقة تعريفية للمنتج/);
});
