import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Panelist — Coordinator Console",
  description: "Placement week scheduling and disruption replanning",
};

/**
 * Two theme-colors so the browser chrome matches the page in both modes. The
 * values are the light and dark `--surface`, which paints the top bar.
 */
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#18181b" },
  ],
};

/**
 * Applies the saved display preferences before first paint.
 *
 * Set in an effect instead, they would land after hydration: a white flash for
 * anyone on dark, a reflow for anyone on a compact board. A blocking script in
 * <head> stamps the root element first. The page does not touch these
 * attributes until it has read the same keys back.
 */
const PREFS_SCRIPT = `
(function () {
  try {
    var d = document.documentElement;
    var t = localStorage.getItem("panelist-theme");
    if (t === "light" || t === "dark") d.setAttribute("data-theme", t);
    var n = localStorage.getItem("panelist-density");
    if (n === "compact" || n === "comfortable") d.setAttribute("data-density", n);
  } catch (e) { /* private mode: fall through to the OS setting */ }
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning silences attribute mismatches one level deep,
    // and both elements have one: <html> is stamped by the script above before
    // hydration, and <body> is where browser extensions inject their own
    // attributes before React loads.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PREFS_SCRIPT }} />
      </head>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
