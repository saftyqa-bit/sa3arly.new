import {
  categoryEntries,
  categoryPath,
  topCategoryEntries,
  topCategoryPath,
} from "../seo-data";
import { urlSet, xmlResponse } from "../sitemap-utils";

const staticPaths = [
  "/",
  "/categories",
  "/cash",
  "/installments",
  "/methodology",
];

export function GET() {
  const productTypes = categoryEntries.filter((entry) => entry.kind === "type");
  return xmlResponse(
    urlSet([
      ...staticPaths.map((path) => ({ path })),
      ...topCategoryEntries.map((category) => ({ path: topCategoryPath(category) })),
      ...productTypes.map((category) => ({ path: categoryPath(category) })),
    ]),
  );
}
