import { absoluteUrl } from "../seo-data";

export function GET() {
  const body = `User-agent: *
Allow: /
Disallow: /api/
Disallow: /admin/
Disallow: /login/
Disallow: /signin-with-chatgpt
Disallow: /signout-with-chatgpt
Disallow: /callback
Disallow: /database/

Sitemap: ${absoluteUrl("/sitemap.xml")}
Sitemap: ${absoluteUrl("/sitemap-products.xml")}
Sitemap: ${absoluteUrl("/sitemap-categories.xml")}
Sitemap: ${absoluteUrl("/sitemap-brands.xml")}
Sitemap: ${absoluteUrl("/sitemap-stores.xml")}
`;
  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
