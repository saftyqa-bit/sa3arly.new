import Link from "next/link";
import {
  cleanText,
  productDisplayName,
  productPath,
  type CatalogProduct,
} from "./seo-data";

export default function ProductLinks({
  products,
  limit = 120,
}: {
  products: CatalogProduct[];
  limit?: number;
}) {
  const visible = products.slice(0, limit);
  return (
    <>
      <div className="seo-product-grid">
        {visible.map((product) => (
          <Link href={productPath(product)} key={product.id}>
            <span>{cleanText(product.type, 80)}</span>
            <b>
              <bdi dir="auto">{productDisplayName(product)}</bdi>
            </b>
            <small>
              {product.specs
                .filter((specification) => specification.name !== "اسم النسخة")
                .slice(0, 2)
                .map(
                  (specification) =>
                    `${cleanText(specification.name, 50)}: ${cleanText(
                      specification.value,
                      70,
                    )}`,
                )
                .join(" · ") || "مواصفات النسخة متاحة في صفحة المنتج"}
            </small>
            <em>قارن المتاجر ←</em>
          </Link>
        ))}
      </div>
      {products.length > visible.length && (
        <p className="seo-list-note">
          تعرض الصفحة {visible.length.toLocaleString("ar-EG-u-nu-latn")} منتجًا
          من أصل {products.length.toLocaleString("ar-EG-u-nu-latn")}؛ وتبقى
          جميع صفحات المنتجات المؤهلة متاحة عبر خريطة الموقع.
        </p>
      )}
    </>
  );
}
