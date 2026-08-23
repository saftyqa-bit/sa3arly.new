import { brandEntries, brandPath } from "../seo-data";
import { urlSet, xmlResponse } from "../sitemap-utils";

export function GET() {
  return xmlResponse(
    urlSet([
      { path: "/brands" },
      ...brandEntries.map((brand) => ({ path: brandPath(brand) })),
    ]),
  );
}
