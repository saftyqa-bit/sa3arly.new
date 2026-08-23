function words(value: unknown) {
  return String(value ?? "").normalize("NFKC").trim().split(/\s+/).filter(Boolean);
}

function same(left: string, right: string) {
  return left.toLocaleLowerCase("en-US") === right.toLocaleLowerCase("en-US");
}

function stripLeadingWords(value: unknown, prefix: unknown) {
  const valueWords = words(value);
  const prefixWords = words(prefix);
  if (!prefixWords.length || valueWords.length < prefixWords.length) return valueWords;
  const matches = prefixWords.every((word, index) => same(word, valueWords[index]));
  return matches ? valueWords.slice(prefixWords.length) : valueWords;
}

export function productNameParts(brand: unknown, model: unknown, variant?: unknown) {
  const brandWords = words(brand);
  const modelWords = stripLeadingWords(model, brand);
  let variantWords = stripLeadingWords(variant, brand);
  variantWords = stripLeadingWords(variantWords.join(" "), modelWords.join(" "));
  const combined = [...brandWords, ...modelWords, ...variantWords];
  return combined.filter((word, index) => index === 0 || !same(word, combined[index - 1])).join(" ");
}

export function modelNameParts(brand: unknown, model: unknown) {
  return productNameParts(brand, model);
}

export function collapseRepeatedName(value: unknown) {
  const valueWords = words(value);
  return valueWords.filter((word, index) => index === 0 || !same(word, valueWords[index - 1])).join(" ");
}
