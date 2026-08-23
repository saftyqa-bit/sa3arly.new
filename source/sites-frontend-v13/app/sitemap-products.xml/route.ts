import { indexableProducts, productPath } from "../seo-data";
import { urlSet, xmlResponse } from "../sitemap-utils";

export function GET() {
  return xmlResponse(
    urlSet(indexableProducts.map((product) => ({ path: productPath(product) }))),
  );
}
