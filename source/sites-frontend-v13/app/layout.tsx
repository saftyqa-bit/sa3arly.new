import type { Metadata } from "next";
import Script from "next/script";
import DecisionPreferenceRestorer from "./decision-preference-restorer";
import { OG_IMAGE_PATH, SITE_NAME, SITE_URL } from "./seo-data";
import "./globals.css";
import "./comparison-redesign.css";
import "./phase-two.css";
import "./phase-two-extra.css";
import "./experience-redesign.css";
import "./visual-system.css";
import "./pricena-inspired.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "سعرلي: قارن الأسعار واعرف أفضل وقت للشراء في مصر",
    template: "%s | سعرلي",
  },
  description:
    "قارن التكلفة النهائية وتاريخ السعر وموثوقية المتجر والضمان والشحن، واختر أوفر سعر أو أضمن شراء أو أسرع توصيل أو أفضل تقسيط.",
  openGraph: {
    locale: "ar_EG",
    type: "website",
    title: "سعرلي: قارن الأسعار واعرف أفضل وقت للشراء في مصر",
    description:
      "مؤشر قرار شراء وتاريخ أسعار وتكلفة نهائية حقيقية ومقارنة الكاش والتقسيط في متاجر مصر.",
    siteName: SITE_NAME,
    images: [
      {
        url: OG_IMAGE_PATH,
        width: 1200,
        height: 630,
        alt: "سعرلي — منتج واحد وكل الأسعار أمامك",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "سعرلي: قارن الأسعار واعرف أفضل وقت للشراء في مصر",
    description:
      "مؤشر قرار شراء وتاريخ أسعار وتكلفة نهائية حقيقية ومقارنة الكاش والتقسيط في متاجر مصر.",
    images: [OG_IMAGE_PATH],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl">
      <body>
        <DecisionPreferenceRestorer />
        {children}
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=G-030V4E9EWT"
          strategy="afterInteractive"
        />
        <Script id="google-analytics" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());

            gtag('config', 'G-030V4E9EWT');
          `}
        </Script>
      </body>
    </html>
  );
}
