import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import ProductLinks from "../../product-links";
import PublicShell from "../../public-shell";
import { safeJsonLd } from "../../product-schema";
import {
  absoluteUrl,
  brandEntries,
  brandPath,
  categoryEntries,
  categoryPath,
  cleanText,
  getCategoryBySlug,
  getTopCategoryForLegacySlug,
  OG_IMAGE_PATH,
  topCategoryPath,
  uniqueNames,
} from "../../seo-data";

type CategoryPageProps = {
  params: Promise<{ slug: string }>;
};

function categoryMetadata(name: string, slug: string) {
  if (slug === "mobiles") {
    return {
      title: "أسعار الموبايلات في مصر ومقارنة المتاجر | سعرلي",
      description:
        "قارن أسعار ومواصفات الموبايلات بين المتاجر المصرية حسب الماركة والموديل والذاكرة واللون وخيارات الكاش والتقسيط.",
    };
  }
  if (slug === "laptops") {
    return {
      title: "أسعار اللابتوبات في مصر ومقارنة المواصفات | سعرلي",
      description:
        "قارن أسعار ومواصفات اللابتوبات بين متاجر مصر حسب الماركة والموديل والذاكرة والتخزين وخيارات الكاش والتقسيط.",
    };
  }
  if (slug === "home-appliances") {
    return {
      title: "أسعار الأجهزة المنزلية في مصر ومقارنة المتاجر | سعرلي",
      description:
        "قارن أسعار ومواصفات الأجهزة المنزلية بين متاجر مصر، واعرض خيارات الكاش والتقسيط والعروض المتاحة قبل الشراء.",
    };
  }
  return {
    title: `أسعار ${cleanText(name, 80)} في مصر ومقارنة المتاجر | سعرلي`,
    description: `قارن أسعار ومواصفات ${cleanText(name, 80)} بين متاجر مصر، واعرض خيارات الكاش والتقسيط والعروض المتاحة قبل الشراء.`,
  };
}

export async function generateMetadata({
  params,
}: CategoryPageProps): Promise<Metadata> {
  const { slug } = await params;
  const topCategory = getTopCategoryForLegacySlug(slug);
  if (topCategory) {
    const canonical = absoluteUrl(topCategoryPath(topCategory));
    return {
      title: { absolute: `أسعار ${topCategory.name} في مصر | سعرلي` },
      alternates: { canonical },
      robots: { index: true, follow: true },
    };
  }
  const category = getCategoryBySlug(slug);
  if (!category || !category.products.length) {
    return {
      title: { absolute: "التصنيف غير متاح | سعرلي" },
      robots: { index: false, follow: false },
    };
  }
  const content = categoryMetadata(category.name, category.slug);
  const canonical = absoluteUrl(categoryPath(category));
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

export default async function CategoryPage({ params }: CategoryPageProps) {
  const { slug } = await params;
  const topCategory = getTopCategoryForLegacySlug(slug);
  if (topCategory) permanentRedirect(topCategoryPath(topCategory));
  const category = getCategoryBySlug(slug);
  if (!category || !category.products.length) notFound();

  const content = categoryMetadata(category.name, category.slug);
  const brands = brandEntries.filter((brand) =>
    brand.products.some((product) =>
      category.products.some((item) => item.id === product.id),
    ),
  );
  const subcategories =
    category.kind === "section"
      ? categoryEntries.filter(
          (entry) =>
            entry.kind === "type" &&
            entry.products.some((product) => product.section === category.name),
        )
      : [];
  const subtypes = uniqueNames(
    category.products.map((product) => product.subtype).filter(Boolean),
  );
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
        item: absoluteUrl(categoryPath(category)),
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
        <span>{category.kind === "section" ? "قطاع" : "تصنيف منتجات"}</span>
        <h1>{content.title.replace(" | سعرلي", "")}</h1>
        <p>{content.description}</p>
        <div className="seo-listing-stats">
          <b>
            {category.products.length.toLocaleString("ar-EG-u-nu-latn")} منتجًا
            حقيقيًا
          </b>
          <b>{brands.length.toLocaleString("ar-EG-u-nu-latn")} ماركة</b>
          {subtypes.length > 0 && (
            <b>{subtypes.length.toLocaleString("ar-EG-u-nu-latn")} تصنيفًا فرعيًا</b>
          )}
        </div>
      </header>

      {(subcategories.length > 0 || subtypes.length > 0) && (
        <section className="seo-directory-section">
          <h2>التصنيفات الفرعية</h2>
          <div className="seo-chip-list">
            {subcategories.map((entry) => (
              <Link href={categoryPath(entry)} key={entry.slug}>
                {entry.name}
              </Link>
            ))}
            {subtypes.map((name) => (
              <span key={name}>{name}</span>
            ))}
          </div>
        </section>
      )}

      <section className="seo-directory-section">
        <h2>الماركات المتاحة</h2>
        <div className="seo-chip-list">
          {brands.map((brand) => (
            <Link href={brandPath(brand)} key={brand.slug}>
              <bdi dir="auto">{brand.name}</bdi>
            </Link>
          ))}
        </div>
      </section>

      <section className="seo-directory-section">
        <h2>منتجات {category.name}</h2>
        <ProductLinks products={category.products} />
      </section>
    </PublicShell>
  );
}
