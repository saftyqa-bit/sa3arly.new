import { getProductBySlug, cleanText } from "../../../seo-data";

function escapeXml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  const product = getProductBySlug(slug);
  if (!product) {
    return new Response("Not found", { status: 404 });
  }
  const brand = escapeXml(cleanText(product.brand, 40));
  const model = escapeXml(cleanText(product.model, 54));
  const type = escapeXml(cleanText(product.type, 44));
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="720" viewBox="0 0 720 720" role="img" aria-label="${brand} ${model}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#071f38"/>
      <stop offset="1" stop-color="#0b5cab"/>
    </linearGradient>
  </defs>
  <rect width="720" height="720" rx="48" fill="url(#bg)"/>
  <circle cx="112" cy="112" r="56" fill="#29a3ff"/>
  <text x="112" y="132" text-anchor="middle" fill="white" font-size="58" font-family="Arial, sans-serif" font-weight="700">س</text>
  <rect x="84" y="210" width="552" height="340" rx="34" fill="white" fill-opacity=".96"/>
  <text x="360" y="322" text-anchor="middle" fill="#0b5cab" font-size="42" font-family="Arial, sans-serif" font-weight="700" direction="ltr">${brand}</text>
  <text x="360" y="405" text-anchor="middle" fill="#10263e" font-size="38" font-family="Arial, sans-serif" font-weight="700" direction="ltr">${model}</text>
  <text x="360" y="478" text-anchor="middle" fill="#536980" font-size="26" font-family="Arial, sans-serif" direction="rtl">${type}</text>
  <text x="636" y="630" text-anchor="end" fill="white" font-size="30" font-family="Arial, sans-serif" font-weight="700" direction="rtl">سعرلي</text>
  <text x="84" y="630" fill="#b9ddff" font-size="20" font-family="Arial, sans-serif" direction="rtl">بطاقة تعريفية للمنتج</text>
</svg>`;
  return new Response(svg, {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      "Cache-Control": "public, max-age=86400, s-maxage=604800",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
