import { storeEntries, storePath } from "../seo-data";
import { urlSet, xmlResponse } from "../sitemap-utils";

export function GET() {
  return xmlResponse(
    urlSet([
      { path: "/stores" },
      ...storeEntries.map((store) => ({ path: storePath(store) })),
    ]),
  );
}
