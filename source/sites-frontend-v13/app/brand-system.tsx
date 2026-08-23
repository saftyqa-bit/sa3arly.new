import type { ReactNode, SVGProps } from "react";

export type Sa3arlyIconName =
  | "arrow"
  | "browse"
  | "cash"
  | "check"
  | "compare"
  | "delivery"
  | "history"
  | "mobile"
  | "search"
  | "shield"
  | "spark"
  | "store";

const paths: Record<Sa3arlyIconName, ReactNode> = {
  arrow: <path d="m9 18 6-6-6-6M15 12H3" />,
  browse: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
  cash: <><rect x="2.5" y="5" width="19" height="14" rx="3" /><path d="M7 12h.01M17 12h.01M12 9.25c1.5 0 2.75 1.2 2.75 2.75S13.5 14.75 12 14.75 9.25 13.55 9.25 12 10.5 9.25 12 9.25Z" /></>,
  check: <path d="m5 12.5 4 4L19 7" />,
  compare: <><path d="M7 3v18M17 3v18" /><path d="m3.5 7 3.5-4 3.5 4M13.5 17l3.5 4 3.5-4" /></>,
  delivery: <><path d="M3 6h11v11H3zM14 10h4l3 3v4h-7z" /><circle cx="7" cy="18" r="2" /><circle cx="18" cy="18" r="2" /></>,
  history: <><path d="M3.5 12a8.5 8.5 0 1 0 2.3-5.8L3.5 8.5" /><path d="M3.5 3.5v5h5M12 7.5V12l3 2" /></>,
  mobile: <><rect x="6.5" y="2" width="11" height="20" rx="3" /><path d="M10 5h4M11 19h2" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>,
  shield: <><path d="M12 2.5 20 6v5.5c0 5-3.2 8.2-8 10-4.8-1.8-8-5-8-10V6l8-3.5Z" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>,
  spark: <><path d="m12 2 1.4 4.6L18 8l-4.6 1.4L12 14l-1.4-4.6L6 8l4.6-1.4L12 2Z" /><path d="m19 14 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14Z" /></>,
  store: <><path d="M4 9v11h16V9" /><path d="M3 9h18l-2-6H5L3 9Z" /><path d="M8 20v-6h8v6" /></>,
};

export function Sa3arlyIcon({
  name,
  ...props
}: { name: Sa3arlyIconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}

export function Sa3arlyMark({ className = "" }: { className?: string }) {
  return (
    <span className={`sa3arly-mark ${className}`.trim()} aria-hidden="true">
      <svg viewBox="0 0 48 48" role="presentation">
        <defs>
          <linearGradient id="sa3arly-mark-gradient" x1="6" y1="5" x2="42" y2="43" gradientUnits="userSpaceOnUse">
            <stop stopColor="#4B8BFF" />
            <stop offset="1" stopColor="#1252D8" />
          </linearGradient>
        </defs>
        <path d="M10 3h23c6.6 0 12 5.4 12 12v18c0 6.6-5.4 12-12 12H15C8.4 45 3 39.6 3 33V10l7-7Z" fill="url(#sa3arly-mark-gradient)" />
        <path d="M13 15.5h22M16.5 15.5l2.7 15.2c.4 2.2 2.3 3.8 4.5 3.8h6.8" stroke="white" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="22" cy="38.2" r="2.3" fill="white" />
        <circle cx="32.5" cy="38.2" r="2.3" fill="white" />
        <path d="m22.5 24 3.2 3.2 6.2-6.7" stroke="white" strokeWidth="2.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

export function Sa3arlyBrand({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`sa3arly-brand ${compact ? "compact" : ""}`.trim()}>
      <Sa3arlyMark />
      <span className="sa3arly-wordmark">
        <b>سعرلي</b>
        {!compact && <small>اختيار أذكى، سعر أوضح</small>}
      </span>
    </span>
  );
}
