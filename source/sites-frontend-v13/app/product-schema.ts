import type { CatalogProduct } from "./seo-data";
import type { LiveCashOffer } from "./live-data";
import { cleanText, sourceUrl } from "./seo-data";

type ProductSchemaInput = {
  product: CatalogProduct;
  canonicalUrl: string;
  cashOffers: LiveCashOffer[];
};

function offerAvailability(value: string | null | undefined) {
  const map: Record<string, string> = {
    available: "https://schema.org/InStock",
    limited: "https://schema.org/LimitedAvailability",
    preorder: "https://schema.org/PreOrder",
    out_of_stock: "https://schema.org/OutOfStock",
  };
  return value ? map[value] : undefined;
}

function schemaOffer(offer: LiveCashOffer) {
  const url = sourceUrl(offer.source_url);
  return {
    "@type": "Offer",
    price: offer.cash_price,
    priceCurrency: "EGP",
    ...(url ? { url } : {}),
    ...(offerAvailability(offer.availability)
      ? { availability: offerAvailability(offer.availability) }
      : {}),
    seller: {
      "@type": "Organization",
      name: cleanText(offer.store_name, 100),
    },
  };
}

export function buildProductStructuredData({
  product,
  canonicalUrl,
  cashOffers,
}: ProductSchemaInput) {
  const offers = cashOffers
    .filter(
      (offer) =>
        Number.isFinite(offer.cash_price) &&
        offer.cash_price > 0 &&
        offer.currency === "EGP",
    )
    .map(schemaOffer);

  const offerData =
    offers.length === 1
      ? offers[0]
      : offers.length > 1
        ? {
            "@type": "AggregateOffer",
            priceCurrency: "EGP",
            lowPrice: Math.min(...cashOffers.map((offer) => offer.cash_price)),
            highPrice: Math.max(...cashOffers.map((offer) => offer.cash_price)),
            offerCount: offers.length,
            offers,
          }
        : undefined;

  return {
    "@context": "https://schema.org",
    "@type": "Product",
    "@id": `${canonicalUrl}#product`,
    name: cleanText(
      [product.brand, product.model, product.variant].filter(Boolean).join(" "),
      180,
    ),
    sku: cleanText(product.id, 80),
    brand: {
      "@type": "Brand",
      name: cleanText(product.brand, 100),
    },
    model: cleanText(product.model, 120),
    category: cleanText(product.type, 100),
    description: cleanText(
      `مواصفات ومقارنة متاجر ${product.brand} ${product.model} في مصر.`,
      180,
    ),
    url: canonicalUrl,
    additionalProperty: product.specs
      .filter((specification) => specification.name !== "اسم النسخة")
      .map((specification) => ({
        "@type": "PropertyValue",
        name: cleanText(specification.name, 100),
        value: cleanText(specification.value, 160),
      })),
    ...(offerData ? { offers: offerData } : {}),
  };
}

export function safeJsonLd(value: unknown) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}
