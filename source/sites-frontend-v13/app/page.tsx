import type { Metadata } from "next";
import catalog from "./catalog-data.json";
import PriceExplorerV2 from "./price-explorer-v2";
import {
  absoluteUrl,
  type CatalogPresence,
  OG_IMAGE_PATH,
  SITE_NAME,
  SITE_URL,
} from "./seo-data";
import { safeJsonLd } from "./product-schema";
import { isVerifiedPresence } from "./verification";

const HOME_TITLE = "سعرلي: قارن الأسعار واعرف أفضل وقت للشراء في مصر";
const HOME_DESCRIPTION =
  "قارن التكلفة النهائية وتاريخ السعر وموثوقية المتجر والضمان والشحن، واختر أوفر سعر أو أضمن شراء أو أسرع توصيل أو أفضل تقسيط.";
const FILTER_PARAMETERS = new Set([
  "brand", "category", "sort", "price", "color", "storage", "installment",
  "model", "page", "product", "q", "search", "type", "mode", "available",
  "warranty", "pickup", "compare",
]);

const presenceByProduct = catalog.presence as unknown as Record<
  string,
  CatalogPresence[]
>;
const productById = new Map(catalog.products.map((product) => [product.id, product]));
const initialVerifiedPublicMappings = Object.entries(presenceByProduct).reduce(
  (total, [productId, stores]) => {
    const product = productById.get(productId);
    if (!product) return total;
    return total + stores.filter((store) => isVerifiedPresence(product, store)).length;
  },
  0,
);

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<Metadata> {
  const params = await searchParams;
  const filtered = Object.keys(params).some((key) => FILTER_PARAMETERS.has(key));
  return {
    title: { absolute: HOME_TITLE },
    description: HOME_DESCRIPTION,
    alternates: { canonical: absoluteUrl("/") },
    robots: filtered
      ? { index: false, follow: true }
      : {
          index: true,
          follow: true,
          "max-image-preview": "large",
          "max-snippet": -1,
          "max-video-preview": -1,
          googleBot: {
            index: true,
            follow: true,
            "max-image-preview": "large",
            "max-snippet": -1,
            "max-video-preview": -1,
          },
        },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      locale: "ar_EG",
      title: HOME_TITLE,
      description: HOME_DESCRIPTION,
      url: absoluteUrl("/"),
      images: [
        {
          url: absoluteUrl(OG_IMAGE_PATH),
          width: 1200,
          height: 630,
          alt: "سعرلي — منتج واحد وكل الأسعار أمامك في مصر",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: HOME_TITLE,
      description: HOME_DESCRIPTION,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
  };
}

export default function Home() {
  const organization = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": `${SITE_URL}/#organization`,
    name: SITE_NAME,
    url: absoluteUrl("/"),
    logo: absoluteUrl("/favicon.svg"),
  };
  const website = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${SITE_URL}/#website`,
    name: SITE_NAME,
    url: absoluteUrl("/"),
    inLanguage: "ar-EG",
    publisher: { "@id": `${SITE_URL}/#organization` },
    potentialAction: {
      "@type": "SearchAction",
      target: `${SITE_URL}/?q={search_term_string}`,
      "query-input": "required name=search_term_string",
    },
  };
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(organization) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(website) }}
      />
      <PriceExplorerV2
        initialStats={{
          products: catalog.stats.products,
          registryStores: catalog.stats.registryStores,
          selectedSectorStores: catalog.stats.selectedSectorStores,
        }}
        initialVerifiedPublicMappings={initialVerifiedPublicMappings}
      />
    </>
  );
}
