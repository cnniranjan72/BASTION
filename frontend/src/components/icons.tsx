// A small hand-written stroke-icon set (Feather-style, 20x20, currentColor)
// instead of an icon library dependency — the product only needs ~14 of
// them, all used at one size, so a library's font/sprite/tree-shaking
// overhead buys nothing a dozen inline SVGs don't already give for free.
import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function base(children: ReactNode, props: IconProps) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const OverviewIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>,
    p,
  );

export const GraphIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="5" cy="6" r="2.3" />
      <circle cx="19" cy="6" r="2.3" />
      <circle cx="12" cy="18" r="2.3" />
      <path d="M6.8 7.6 10.3 16.3M17.2 7.6 13.7 16.3M7.3 6h9.4" />
    </>,
    p,
  );

export const TracesIcon = (p: IconProps) =>
  base(
    <>
      <path d="M4 6h16M4 12h10M4 18h16" />
      <circle cx="19" cy="12" r="1.6" />
    </>,
    p,
  );

export const AnalyticsIcon = (p: IconProps) =>
  base(
    <>
      <path d="M4 20V10M11 20V4M18 20v-7" />
      <path d="M3 20h18" />
    </>,
    p,
  );

export const AgentsIcon = (p: IconProps) =>
  base(
    <>
      <rect x="4" y="8" width="16" height="11" rx="2" />
      <path d="M12 8V4M9 4h6" />
      <circle cx="9" cy="13.5" r="1.2" />
      <circle cx="15" cy="13.5" r="1.2" />
    </>,
    p,
  );

export const PoliciesIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 3 5 6v6c0 4.2 3 7.4 7 9 4-1.6 7-4.8 7-9V6z" />
      <path d="m9.2 12.2 2 2 3.6-4" />
    </>,
    p,
  );

export const ApprovalsIcon = (p: IconProps) =>
  base(
    <>
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <path d="m8.5 12.5 2.2 2.2L16 9.5" />
    </>,
    p,
  );

export const TeamIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
      <circle cx="17.5" cy="8.5" r="2.3" />
      <path d="M16 13.7c2.4.3 4.5 2.1 4.5 4.8" />
    </>,
    p,
  );

export const AccountIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.5 20c0-4 3.4-6.5 7.5-6.5s7.5 2.5 7.5 6.5" />
    </>,
    p,
  );

export const KeyIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="8" cy="15" r="3.5" />
      <path d="m10.3 12.6 8-8M15 8.5l2 2M18 5.5l2 2" />
    </>,
    p,
  );

export const SearchIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m19.5 19.5-4.3-4.3" />
    </>,
    p,
  );

export const PlusIcon = (p: IconProps) => base(<path d="M12 4.5v15M4.5 12h15" />, p);

export const AlertIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 3 2 20h20z" />
      <path d="M12 9.5v4.5M12 17h.01" />
    </>,
    p,
  );

export const EmptyBoxIcon = (p: IconProps) =>
  base(
    <>
      <path d="M3.5 8 12 4l8.5 4-8.5 4-8.5-4Z" />
      <path d="M3.5 8v8L12 20l8.5-4V8M12 12v8" />
    </>,
    p,
  );

// U16 (v2 upgrade) — Threat Center.
export const ThreatIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 3.5 4.5 6.5v5c0 5 3.2 7.8 7.5 9 4.3-1.2 7.5-4 7.5-9v-5Z" />
      <path d="M9.3 12.2l1.9 1.9 3.5-3.9" />
    </>,
    p,
  );

// U16 (v2 upgrade) — Cost Center.
export const CostIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7.5v9M14.7 9.7c-.4-.9-1.4-1.5-2.7-1.5-1.7 0-3 .9-3 2s1.3 1.8 3 2 3 .9 3 2-1.3 2-3 2c-1.3 0-2.3-.6-2.7-1.5" />
    </>,
    p,
  );

// U16 (v2 upgrade) — Agent Health.
export const HealthIcon = (p: IconProps) =>
  base(<path d="M3.5 12h4l2-6 3 12 2-8 1.5 2h4.5" />, p);
