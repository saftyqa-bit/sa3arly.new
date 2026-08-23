import { absoluteUrl, DATA_LAST_MODIFIED } from "./seo-data";

export type SitemapUrl = {
  path: string;
  lastmod?: string;
};

function escapeXml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function xmlResponse(xml: string) {
  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export function urlSet(items: SitemapUrl[]) {
  const urls = items
    .map(
      (item) => `<url>
  <loc>${escapeXml(absoluteUrl(item.path))}</loc>
  <lastmod>${escapeXml(item.lastmod ?? DATA_LAST_MODIFIED)}</lastmod>
</url>`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>`;
}

export function sitemapIndex(paths: string[]) {
  const entries = paths
    .map(
      (path) => `<sitemap>
  <loc>${escapeXml(absoluteUrl(path))}</loc>
  <lastmod>${escapeXml(DATA_LAST_MODIFIED)}</lastmod>
</sitemap>`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${entries}
</sitemapindex>`;
}
