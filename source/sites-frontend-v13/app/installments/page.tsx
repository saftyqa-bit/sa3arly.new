import type { Metadata } from "next";
import ComparisonLanding, { parseListingFilters, parsePageParam } from "../comparison-landing";
import { absoluteUrl, OG_IMAGE_PATH } from "../seo-data";

export const dynamic = "force-dynamic";

const title = "مقارنة تقسيط المنتجات والدفعات في مصر | سعرلي";
const description =
  "قارن القسط والمقدم والمدة والإجمالي للمنتجات التي لها خطط تقسيط مؤكدة بين المتاجر ومقدمي التمويل.";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ searchParams }: PageProps): Promise<Metadata> {
  const page = parsePageParam(await searchParams);
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: absoluteUrl("/installments") },
    robots: page > 1 ? { index: false, follow: true } : { index: true, follow: true },
    openGraph: {
      type: "website",
      siteName: "سعرلي",
      locale: "ar_EG",
      title,
      description,
      url: absoluteUrl("/installments"),
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [absoluteUrl(OG_IMAGE_PATH)],
    },
  };
}

export default async function InstallmentsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const page = parsePageParam(params);
  const filters = parseListingFilters(params);
  return <ComparisonLanding mode="installments" page={page} {...filters} />;
}
