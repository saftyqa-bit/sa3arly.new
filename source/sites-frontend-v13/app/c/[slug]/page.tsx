import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ProductLinks from "../../product-links";
import PublicShell from "../../public-shell";
import { safeJsonLd } from "../../product-schema";
import {
  productMatchesTaxonomyLeaf,
  taxonomyLeafCount,
  taxonomyLeaves,
  topCategoryDefinitionForSection,
  type ProductTaxonomyLeaf,
} from "../../category-taxonomy";
import {
  absoluteUrl,
  type CatalogProduct,
  getTopCategoryBySlug,
  OG_IMAGE_PATH,
  topCategoryPath,
} from "../../seo-data";

type TopCategoryPageProps = {
  params: Promise<{ slug: string }>;
};

function categoryMetadata(name: string) {
  return {
    title: `أسعار ${name} في مصر ومقارنة المتاجر | سعرلي`,
    description: `قارن أسعار ومواصفات ${name} بين متاجر مصر، واعرض أسعار الكاش وخيارات التقسيط والعروض المتاحة قبل الشراء.`,
  };
}

const specificationOrder = [
  "الذاكرة (RAM)",
  "السعة التخزينية",
  "الحمولة بالكيلوجرام",
  "السعة باللتر",
  "مقاس الشاشة",
  "تقنية الشاشة",
  "الدقة",
  "نوع التحميل",
  "اللون",
];

function productsForLeaf(
  products: CatalogProduct[],
  leaf: ProductTaxonomyLeaf,
) {
  return products.filter((product) =>
    productMatchesTaxonomyLeaf(product, leaf),
  );
}

function uniqueModelCount(products: CatalogProduct[]) {
  return new Set(
    products.map((product) => `${product.brand}\u0000${product.model}`),
  ).size;
}

function brandSummaries(products: CatalogProduct[]) {
  const summaries = new Map<
    string,
    { name: string; variants: number; models: Set<string> }
  >();
  for (const product of products) {
    const current = summaries.get(product.brand);
    if (current) {
      current.variants += 1;
      current.models.add(product.model);
    } else {
      summaries.set(product.brand, {
        name: product.brand,
        variants: 1,
        models: new Set([product.model]),
      });
    }
  }
  return [...summaries.values()].sort(
    (left, right) =>
      right.variants - left.variants ||
      left.name.localeCompare(right.name, "ar", { numeric: true }),
  );
}

function specificationDimensions(products: CatalogProduct[]) {
  const names = new Set<string>();
  for (const product of products) {
    for (const specification of product.specs) {
      if (specification.name && specification.name !== "اسم النسخة") {
        names.add(specification.name);
      }
    }
  }
  return [...names].sort((left, right) => {
    const leftIndex = specificationOrder.indexOf(left);
    const rightIndex = specificationOrder.indexOf(right);
    const leftRank = leftIndex === -1 ? specificationOrder.length : leftIndex;
    const rightRank = rightIndex === -1 ? specificationOrder.length : rightIndex;
    return (
      leftRank - rightRank ||
      left.localeCompare(right, "ar", { numeric: true })
    );
  });
}

function selectorHref(
  section: string,
  leaf?: ProductTaxonomyLeaf,
  brand?: string,
) {
  const search = new URLSearchParams({ category: section });
  if (leaf) search.set("type", leaf.name);
  if (brand) search.set("brand", brand);
  return `/?${search.toString()}#selector`;
}

export async function generateMetadata({
  params,
}: TopCategoryPageProps): Promise<Metadata> {
  const { slug } = await params;
  const category = getTopCategoryBySlug(slug);
  if (!category) {
    return {
      title: { absolute: "التصنيف غير متاح | سعرلي" },
      robots: { index: false, follow: false },
    };
  }

  const content = categoryMetadata(category.name);
  const canonical = absoluteUrl(topCategoryPath(category));
  return {
    title: { absolute: content.title },
    description: content.description,
    alternates: { canonical },
    robots: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
      googleBot: {
        index: true,
        follow: true,
        "max-image-preview": "large",
        "max-snippet": -1,
        "max-video-preview": -1,
      },
    },
    openGraph: {
      type: "website",
      siteName: "سعرلي",
      locale: "ar_EG",
      title: content.title,
      description: content.description,
      url: canonical,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
    twitter: {
      card: "summary_large_image",
      title: content.title,
      description: content.description,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
  };
}

export default async function TopCategoryPage({ params }: TopCategoryPageProps) {
  const { slug } = await params;
  const category = getTopCategoryBySlug(slug);
  if (!category) notFound();

  const content = categoryMetadata(category.name);
  const taxonomy = topCategoryDefinitionForSection(category.sectionName);
  const categoryBrands = brandSummaries(category.allProducts);
  const namedGroupCount =
    taxonomy?.groups.filter((group) => Boolean(group.name)).length ?? 0;
  const directTypeCount = taxonomy
    ? taxonomyLeaves(taxonomy).length
    : 0;
  const canonicalPath = topCategoryPath(category);
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "سعرلي",
        item: absoluteUrl("/"),
      },
      {
        "@type": "ListItem",
        position: 2,
        name: "التصنيفات",
        item: absoluteUrl("/categories"),
      },
      {
        "@type": "ListItem",
        position: 3,
        name: category.name,
        item: absoluteUrl(canonicalPath),
      },
    ],
  };

  return (
    <PublicShell
      breadcrumbs={[
        { href: "/", label: "الرئيسية" },
        { href: "/categories", label: "التصنيفات" },
        { label: category.name },
      ]}
    >
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: safeJsonLd(breadcrumb) }}
      />
      <header className="seo-listing-hero">
        <span>قسم رئيسي</span>
        <h1>{content.title.replace(" | سعرلي", "")}</h1>
        <p>{content.description}</p>
        <div className="seo-listing-stats">
          <b>
            {category.allProducts.length.toLocaleString("ar-EG-u-nu-latn")} منتجًا
            في قاعدة البيانات
          </b>
          <b>{categoryBrands.length.toLocaleString("ar-EG-u-nu-latn")} ماركة</b>
          <b>
            {namedGroupCount
              ? `${namedGroupCount.toLocaleString("ar-EG-u-nu-latn")} تقسيمات`
              : `${directTypeCount.toLocaleString("ar-EG-u-nu-latn")} أنواع`}
          </b>
        </div>
      </header>

      {taxonomy && (
        <section className="seo-directory-section">
          <h2>أقسام وفئات {category.name}</h2>
          <div className="taxonomy-groups">
            {taxonomy.groups.map((group, groupIndex) => {
              const groupCount = group.items.reduce(
                (total, item) =>
                  total + taxonomyLeafCount(category.allProducts, item),
                0,
              );
              return (
                <article
                  className={group.name ? "" : "taxonomy-direct"}
                  key={group.name ?? `direct-${groupIndex}`}
                >
                  {group.name && (
                    <h3>
                      {group.name}
                      <small>
                        {groupCount.toLocaleString("ar-EG-u-nu-latn")}
                      </small>
                    </h3>
                  )}
                  <div className="seo-chip-list">
                    {group.items.map((item) => {
                      const count = taxonomyLeafCount(
                        category.allProducts,
                        item,
                      );
                      const products = productsForLeaf(
                        category.allProducts,
                        item,
                      );
                      const modelCount = uniqueModelCount(products);
                      const href = `/?category=${encodeURIComponent(category.sectionName)}&type=${encodeURIComponent(item.name)}#selector`;
                      return (
                        <Link href={href} key={item.name}>
                          {item.name}
                          <small>
                            {count.toLocaleString("ar-EG-u-nu-latn")} نسخة ·{" "}
                            {modelCount.toLocaleString("ar-EG-u-nu-latn")} موديل
                          </small>
                        </Link>
                      );
                    })}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      <section className="seo-directory-section">
        <h2>الماركات المتاحة في {category.name}</h2>
        <div className="seo-chip-list">
          {categoryBrands.map((brand) => (
            <Link
              href={selectorHref(category.sectionName, undefined, brand.name)}
              key={brand.name}
            >
              <bdi dir="auto">{brand.name}</bdi>
              <small>{brand.variants.toLocaleString("ar-EG-u-nu-latn")}</small>
            </Link>
          ))}
        </div>
      </section>

      {taxonomy && (
        <section className="seo-directory-section catalog-hierarchy-section">
          <div className="catalog-hierarchy-heading">
            <div>
              <span>الشجرة الحية من قاعدة سعرلي</span>
              <h2>الماركات والموديلات والمواصفات داخل كل نوع</h2>
            </div>
            <p>
              الماركات مرتبة بعدد النسخ. بعد اختيار الماركة تظهر الموديلات،
              ثم تظهر فقط قوائم المواصفات المتاحة فعليًا للموديل المختار.
            </p>
          </div>
          <div className="catalog-level-list">
            {taxonomyLeaves(taxonomy).map((leaf, index) => {
              const products = productsForLeaf(category.allProducts, leaf);
              const brands = brandSummaries(products);
              const dimensions = specificationDimensions(products);
              const modelCount = uniqueModelCount(products);
              return (
                <details className="catalog-level-card" open={index === 0} key={leaf.name}>
                  <summary>
                    <span>
                      <b>{leaf.name}</b>
                      <small>
                        {products.length.toLocaleString("ar-EG-u-nu-latn")} نسخة /{" "}
                        {modelCount.toLocaleString("ar-EG-u-nu-latn")} موديل
                      </small>
                    </span>
                    <i aria-hidden="true">+</i>
                  </summary>
                  <div className="catalog-level-body">
                    <div>
                      <h3>الماركات</h3>
                      <div className="catalog-brand-list">
                        {brands.map((brand) => (
                          <Link
                            href={selectorHref(
                              category.sectionName,
                              leaf,
                              brand.name,
                            )}
                            key={brand.name}
                          >
                            <bdi dir="auto">{brand.name}</bdi>
                            <span>
                              {brand.variants.toLocaleString("ar-EG-u-nu-latn")} نسخة
                              {" · "}
                              {brand.models.size.toLocaleString("ar-EG-u-nu-latn")} موديل
                            </span>
                          </Link>
                        ))}
                      </div>
                    </div>
                    <div className="catalog-dimensions">
                      <h3>بعد اختيار الموديل</h3>
                      {dimensions.length ? (
                        <div>
                          {dimensions.map((dimension) => (
                            <span key={dimension}>{dimension}</span>
                          ))}
                        </div>
                      ) : (
                        <p>تظهر النسخة أو المقاس المتاح مباشرة من القاعدة.</p>
                      )}
                      <Link href={selectorHref(category.sectionName, leaf)}>
                        ابدأ من {leaf.name} ←
                      </Link>
                    </div>
                  </div>
                </details>
              );
            })}
          </div>
        </section>
      )}

      <section className="seo-directory-section">
        <h2>منتجات {category.name} المتاحة للمقارنة</h2>
        {category.products.length > 0 ? (
          <ProductLinks products={category.products} />
        ) : (
          <div className="seo-empty-state">
            <b>لا توجد منتجات مؤكدة للعرض حاليًا.</b>
            <p>ستظهر المنتجات هنا بعد اكتمال التحقق من الموديل ومصدر المتجر.</p>
          </div>
        )}
      </section>
    </PublicShell>
  );
}
