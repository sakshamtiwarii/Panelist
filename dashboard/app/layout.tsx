import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Panelist — Coordinator Console",
  description: "Placement week scheduling and disruption replanning",
};

/**
 * Applies the saved theme before first paint.
 *
 * Without this the theme is only set in an effect, which runs after hydration —
 * so a coordinator who picked dark gets a white flash on every load. Reading
 * localStorage in a blocking script in <head> stamps the root element before
 * the browser paints anything.
 *
 * It also means <html> is modified before React hydrates, which is why <html>
 * carries suppressHydrationWarning below.
 */
const THEME_SCRIPT = `
(function () {
  try {
    var t = localStorage.getItem("panelist-theme");
    if (t === "light" || t === "dark") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) { /* private mode: fall through to the OS setting */ }
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning is required on both elements, and only silences
    // attribute mismatches one level deep:
    //   <html> — the theme script above stamps data-theme pre-hydration.
    //   <body> — browser extensions (Grammarly, password managers) inject
    //            their own attributes before React loads. That mismatch is
    //            the extension's, not the app's, and there is no way to
    //            prevent it from inside the page.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
