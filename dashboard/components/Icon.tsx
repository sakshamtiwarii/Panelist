/**
 * The console's icon set: stroke icons on a 24-grid, inline rather than from a
 * package so the dashboard keeps its three dependencies.
 *
 * Every icon sits beside its own label, so none of them carries meaning alone.
 */

export type IconName =
  | "grid" | "clock" | "late" | "panel" | "student" | "room"
  | "plus" | "minus" | "userPlus" | "userMinus"
  | "warning" | "alert" | "history" | "sliders" | "help" | "close"
  | "chevron" | "sun" | "moon" | "auto" | "logout" | "check"
  | "layers" | "menu" | "arrowRight";

const PATHS: Record<IconName, React.ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.2 1.9" /></>,
  late: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.2 1.9" /><path d="M19 5l2-2" /></>,
  panel: <><path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20" /><circle cx="10" cy="7.5" r="3.2" /><path d="M17.5 11.5 21 8M21 11.5 17.5 8" /></>,
  student: <><path d="M3 8.5 12 4l9 4.5-9 4.5-9-4.5Z" /><path d="M7 10.7V15c0 1.4 2.2 2.6 5 2.6s5-1.2 5-2.6v-4.3" /></>,
  room: <><path d="M4 20V6.2a1 1 0 0 1 .7-.95l7-2.2a1 1 0 0 1 1.3.95V20" /><path d="M13 9h6a1 1 0 0 1 1 1v10" /><path d="M2.5 20h19" /><path d="M9.5 12.5h.01" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  minus: <><path d="M5 12h14" /></>,
  userPlus: <><path d="M15 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-4A3.5 3.5 0 0 0 4 18.5V20" /><circle cx="9.5" cy="7.5" r="3.3" /><path d="M18 8v6M21 11h-6" /></>,
  userMinus: <><path d="M15 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-4A3.5 3.5 0 0 0 4 18.5V20" /><circle cx="9.5" cy="7.5" r="3.3" /><path d="M21 11h-6" /></>,
  warning: <><path d="M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20.2h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4.5M12 17h.01" /></>,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5M12 16.2h.01" /></>,
  history: <><path d="M3.2 10.5A9 9 0 1 1 5 15.5" /><path d="M3 5.5v5h5" /><path d="M12 7.8v4.6l3 1.8" /></>,
  sliders: <><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2.2" /><circle cx="10" cy="17" r="2.2" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6v.4" /><path d="M12 16.8h.01" /></>,
  close: <><path d="M6 6l12 12M18 6 6 18" /></>,
  chevron: <><path d="m9 5 7 7-7 7" /></>,
  arrowRight: <><path d="M4 12h15M13 6l6 6-6 6" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.5 8.5 0 1 0 20 14.2Z" /></>,
  auto: <><circle cx="12" cy="12" r="9" /><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18" fill="currentColor" stroke="none" /></>,
  logout: <><path d="M9 20H5.5A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4H9" /><path d="M15.5 16.5 20 12l-4.5-4.5M20 12H9.5" /></>,
  check: <><path d="m5 12.5 4.5 4.5L19 7" /></>,
  layers: <><path d="m12 3 8.5 4.5L12 12 3.5 7.5 12 3Z" /><path d="m3.5 12.5 8.5 4.5 8.5-4.5" /><path d="m3.5 16.8 8.5 4.5 8.5-4.5" /></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
};

export default function Icon({
  name, size = 16, strokeWidth = 1.7, className, style,
}: {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flex: "none", ...style }}
      aria-hidden
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/**
 * The wordmark: three room columns with interviews stacked in them at different
 * times. Solid rather than stroked, because it renders down to 16px in a
 * browser tab where stroke icons close up.
 */
export function BrandMark({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden focusable="false">
      <rect x="3" y="3" width="4.6" height="8.4" rx="1.3" />
      <rect x="3" y="13.6" width="4.6" height="7.4" rx="1.3" opacity="0.55" />
      <rect x="9.7" y="6.6" width="4.6" height="14.4" rx="1.3" opacity="0.78" />
      <rect x="16.4" y="3" width="4.6" height="5.2" rx="1.3" opacity="0.55" />
      <rect x="16.4" y="10.4" width="4.6" height="10.6" rx="1.3" />
    </svg>
  );
}
