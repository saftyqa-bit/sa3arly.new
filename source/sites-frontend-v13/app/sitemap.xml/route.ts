import { sitemapIndex, xmlResponse } from "../sitemap-utils";

export function GET() {
  return xmlResponse(
    sitemapIndex([
      "/sitemap-products.xml",
      "/sitemap-categories.xml",
      "/sitemap-brands.xml",
      "/sitemap-stores.xml",
    ]),
  );
}
