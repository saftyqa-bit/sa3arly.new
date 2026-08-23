import catalog from "./catalog-data.json";
import {
  TOP_CATEGORY_DEFINITIONS,
  topCategoryDefinitionForSection,
} from "./category-taxonomy";
import { productNameParts } from "./display-names";

export const SITE_URL = "https://sa3arly.com";
export const SITE_NAME = "سعرلي";
export const OG_IMAGE_PATH = "/og-image.png";
export const DATA_LAST_MODIFIED = new Date(catalog.generatedAt).toISOString();

export type CatalogProduct = (typeof catalog.products)[number];
export type CatalogPresence = (typeof catalog.presence)[keyof typeof catalog.presence][number];

export type CategoryEntry = {
  kind: "section" | "type";
  name: string;
  slug: string;
  products: CatalogProduct[];
};

export type TopCategoryEntry = {
  name: string;
  sectionName: string;
  slug: string;
  products: CatalogProduct[];
  allProducts: CatalogProduct[];
};

export type BrandEntry = {
  name: string;
  slug: string;
  products: CatalogProduct[];
};

export type StoreEntry = {
  id: string;
  name: string;
  slug: string;
  entityType: string;
  products: CatalogProduct[];
};

const CONTROL_OR_MARKUP = /[\u0000-\u001f\u007f<>]/g;

export function cleanText(value: unknown, maxLength = 180) {
  return String(value ?? "")
    .replace(CONTROL_OR_MARKUP, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

export function absoluteUrl(pathname: string) {
  return new URL(pathname, `${SITE_URL}/`).toString();
}

export function slugify(value: string) {
  const slug = cleanText(value, 120)
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "item";
}

export function productSlug(product: CatalogProduct) {
  const prefix = [product.brand, product.model]
    .map((value) => slugify(value))
    .filter(Boolean)
    .join("-");
  return `${prefix}-${product.id.toLocaleLowerCase("en-US")}`;
}

export function productPath(product: CatalogProduct) {
  return `/products/${productSlug(product)}`;
}

export function getProductBySlug(slug: string) {
  const id = slug.match(/(var-[a-z0-9]{6,40})$/i)?.[1]?.toUpperCase();
  return id ? catalog.products.find((product) => product.id === id) ?? null : null;
}

export function getProductPresence(productId: string): CatalogPresence[] {
  return (
    (catalog.presence as unknown as Record<string, CatalogPresence[]>)[productId] ??
    []
  );
}

const EVIDENCE_TOKEN_STOPLIST = new Set([
  "gb",
  "ram",
  "storage",
  "w",
  "kg",
  "eg",
  "en",
  "p",
  "5g",
  "4g",
  "lte",
  "dual",
  "sim",
  "كجم",
  "لتر",
  "بوصة",
  "وات",
  "سم",
  "مل",
]);

const EVIDENCE_SPECIFICATION =
  /اللون|السعة|ram|الذاكرة|الحمولة|مقاس|الحجم/i;

function evidenceTokens(value: unknown) {
  let text = cleanText(value, 500);
  try {
    text = decodeURIComponent(text);
  } catch {
    // Keep the original source text when it contains malformed URL escapes.
  }
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
        !EVIDENCE_TOKEN_STOPLIST.has(token),
    );
}

function sourceContainsValue(sourceTokens: Set<string>, value: unknown) {
  const expected = evidenceTokens(value);
  return expected.length > 0 && expected.every((token) => sourceTokens.has(token));
}

export function isVerifiedPresence(
  product: CatalogProduct,
  presence: CatalogPresence,
) {
  if (
    presence.matchConfidence !== "عالية" ||
    presence.reviewStatus !== "جاهز لأول رصد"
  ) {
    return false;
  }

  if (presence.linkType === "تغذية منتجات") return true;
  if (presence.linkType !== "رابط منتج مباشر") return false;

  const sourceTokens = new Set(evidenceTokens(presence.sourceUrl));
  if (
    !sourceContainsValue(sourceTokens, product.brand) ||
    !sourceContainsValue(sourceTokens, product.model)
  ) {
    return false;
  }

  return product.specs
    .filter((specification) =>
      EVIDENCE_SPECIFICATION.test(specification.name),
    )
    .every((specification) =>
      sourceContainsValue(sourceTokens, specification.value),
    );
}

export function getVerifiedProductPresence(productId: string) {
  const product =
    catalog.products.find((candidate) => candidate.id === productId) ?? null;
  if (!product) return [];
  return getProductPresence(productId).filter((presence) =>
    isVerifiedPresence(product, presence),
  );
}

export function isIndexableProduct(product: CatalogProduct) {
  return Boolean(
    cleanText(product.brand) &&
      cleanText(product.model) &&
      cleanText(product.type) &&
      getVerifiedProductPresence(product.id).length,
  );
}

export const indexableProducts = catalog.products.filter(isIndexableProduct);

export const topCategoryEntries: TopCategoryEntry[] =
  TOP_CATEGORY_DEFINITIONS.map((category) => ({
    ...category,
    products: indexableProducts.filter(
      (product) => product.section === category.sectionName,
    ),
    allProducts: catalog.products.filter(
      (product) => product.section === category.sectionName,
    ),
  }));

export function topCategoryPath(category: TopCategoryEntry) {
  return `/c/${category.slug}`;
}

export function getTopCategoryBySlug(slug: string) {
  return topCategoryEntries.find((entry) => entry.slug === slug) ?? null;
}

export function productTopCategory(product: CatalogProduct) {
  const definition = topCategoryDefinitionForSection(product.section);
  return definition
    ? topCategoryEntries.find((entry) => entry.slug === definition.slug) ?? null
    : null;
}

function categorySlug(kind: CategoryEntry["kind"], name: string) {
  if (kind === "type" && name === "هواتف") return "mobiles";
  if (kind === "type" && name === "لابتوبات") return "laptops";
  if (kind === "section" && name === "الأجهزة المنزلية") {
    return "home-appliances";
  }
  return `${kind === "section" ? "section" : "category"}-${slugify(name)}`;
}

const sectionEntries: CategoryEntry[] = catalog.sections
  .map((section) => ({
    kind: "section" as const,
    name: cleanText(section.name, 100),
    slug: categorySlug("section", section.name),
    products: indexableProducts.filter(
      (product) => product.section === section.name,
    ),
  }))
  .filter((entry) => entry.products.length);

const typeEntries: CategoryEntry[] = catalog.types
  .map((type) => ({
    kind: "type" as const,
    name: cleanText(type.name, 100),
    slug: categorySlug("type", type.name),
    products: indexableProducts.filter((product) => product.type === type.name),
  }))
  .filter((entry) => entry.products.length);

export const categoryEntries = [...sectionEntries, ...typeEntries];

export function categoryPath(category: CategoryEntry) {
  return `/categories/${category.slug}`;
}

export function getCategoryBySlug(slug: string) {
  let normalizedSlug = slug;
  try {
    normalizedSlug = decodeURIComponent(slug);
  } catch {
    // Keep malformed route segments unchanged so they resolve to notFound().
  }
  return categoryEntries.find((entry) => entry.slug === normalizedSlug) ?? null;
}

export function getTopCategoryForLegacySlug(slug: string) {
  const direct = getTopCategoryBySlug(slug);
  if (direct) return direct;

  const legacy = getCategoryBySlug(slug);
  if (legacy?.kind === "section") {
    return (
      topCategoryEntries.find(
        (entry) => entry.sectionName === legacy.name,
      ) ?? null
    );
  }

  // /categories/mobiles previously represented phones only. It now resolves
  // to the broader user-facing mobiles and communications category.
  if (slug === "mobiles") return getTopCategoryBySlug("mobiles");
  return null;
}

const brandsBySlug = new Map<string, BrandEntry>();
for (const product of indexableProducts) {
  const name = cleanText(product.brand, 100);
  if (!name) continue;
  const slug = slugify(name);
  const current = brandsBySlug.get(slug);
  if (current) {
    current.products.push(product);
  } else {
    brandsBySlug.set(slug, { name, slug, products: [product] });
  }
}

export const brandEntries = [...brandsBySlug.values()].sort(
  (left, right) =>
    right.products.length - left.products.length ||
    left.name.localeCompare(right.name, "ar", { numeric: true }),
);

export function brandPath(brand: BrandEntry) {
  return `/brands/${brand.slug}`;
}

export function getBrandBySlug(slug: string) {
  return brandsBySlug.get(slug) ?? null;
}

const storesById = new Map<string, StoreEntry>();
for (const product of indexableProducts) {
  for (const presence of getVerifiedProductPresence(product.id)) {
    const id = cleanText(presence.storeId, 80);
    const name = cleanText(presence.storeName, 100);
    if (!id || !name) continue;
    const current = storesById.get(id);
    if (current) {
      if (!current.products.some((item) => item.id === product.id)) {
        current.products.push(product);
      }
    } else {
      storesById.set(id, {
        id,
        name,
        slug: `${slugify(name)}-${slugify(id)}`,
        entityType: cleanText(presence.entityType, 100),
        products: [product],
      });
    }
  }
}

export const storeEntries = [...storesById.values()].sort((left, right) =>
  left.name.localeCompare(right.name, "ar", { numeric: true }),
);

export function storePath(store: StoreEntry) {
  return `/stores/${store.slug}`;
}

export function getStoreBySlug(slug: string) {
  return storeEntries.find((entry) => entry.slug === slug) ?? null;
}

export function productDisplayName(product: CatalogProduct) {
  return cleanText(productNameParts(product.brand, product.model, product.variant), 180);
}

export function productCategory(product: CatalogProduct) {
  return (
    typeEntries.find((entry) => entry.name === product.type) ??
    sectionEntries.find((entry) => entry.name === product.section) ??
    null
  );
}

export function sourceUrl(value: unknown) {
  try {
    const url = new URL(String(value ?? ""));
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

export function uniqueNames(values: string[]) {
  return [...new Set(values.map((value) => cleanText(value, 100)).filter(Boolean))].sort(
    (left, right) => left.localeCompare(right, "ar", { numeric: true }),
  );
}

export { catalog };
