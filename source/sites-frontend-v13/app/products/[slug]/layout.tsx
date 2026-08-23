import type { ReactNode } from "react";
import ProductDecisionEnhancer from "../../product-decision-enhancer";
import { getProductBySlug } from "../../seo-data";

export default async function ProductLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const product = getProductBySlug(slug);
  return (
    <>
      {children}
      {product && <ProductDecisionEnhancer product={product} />}
    </>
  );
}
