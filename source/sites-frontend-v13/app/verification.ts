const CONTROL_OR_MARKUP = /[\u0000-\u001f\u007f<>]/g;

export function cleanText(value: unknown, maxLength = 180) {
  return String(value ?? "")
    .replace(CONTROL_OR_MARKUP, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

const EVIDENCE_TOKEN_STOPLIST = new Set([
  "gb", "ram", "storage", "w", "kg", "eg", "en", "p", "5g", "4g", "lte",
  "dual", "sim", "كجم", "لتر", "بوصة", "وات", "سم", "مل",
]);

const EVIDENCE_SPECIFICATION = /اللون|السعة|ram|الذاكرة|الحمولة|مقاس|الحجم/i;

function evidenceTokens(value: unknown) {
  let text = cleanText(value, 500);
  try {
    text = decodeURIComponent(text);
  } catch {
    // Preserve malformed source text and fail token matching safely.
  }
  return text
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(/\s+/)
    .map((token) =>
      token.replace(
        /^(\d+(?:\.\d+)?)(?:gb|tb|mb|kg|g|l|ml|w|inch|inches|hz|cm|mm)$/i,
        "$1",
      ),
    )
    .filter(
      (token) =>
        token &&
        (token.length > 1 || /^\d$/.test(token)) &&
        !EVIDENCE_TOKEN_STOPLIST.has(token),
    );
}

function sourceContainsValue(sourceTokens: Set<string>, value: unknown) {
  const expected = evidenceTokens(value);
  return expected.length > 0 && expected.every((token) => sourceTokens.has(token));
}

export type VerifiableProduct = {
  brand: string;
  model: string;
  specs: { name: string; value: string }[];
};

export type VerifiablePresence = {
  matchConfidence: string;
  reviewStatus: string;
  linkType: string;
  sourceUrl: string;
};

export function isVerifiedPresence(
  product: VerifiableProduct,
  presence: VerifiablePresence,
) {
  if (
    presence.matchConfidence !== "عالية" ||
    presence.reviewStatus !== "جاهز لأول رصد"
  ) {
    return false;
  }
  if (presence.linkType === "تغذية منتجات") return true;
  if (presence.linkType !== "رابط منتج مباشر") return false;

  const sourceTokens = new Set(evidenceTokens(presence.sourceUrl));
  if (
    !sourceContainsValue(sourceTokens, product.brand) ||
    !sourceContainsValue(sourceTokens, product.model)
  ) {
    return false;
  }

  return product.specs
    .filter((specification) => EVIDENCE_SPECIFICATION.test(specification.name))
    .every((specification) => sourceContainsValue(sourceTokens, specification.value));
}
