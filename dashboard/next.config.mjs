/**
 * Same-origin in production, direct cross-origin in local dev.
 *
 * When PANELIST_API_ORIGIN is set (a deployment), the browser is given the
 * relative base "/api" and this server proxies those requests onward. The page
 * and the API then share one origin, which matters more than it sounds:
 * platform subdomains sit on the Public Suffix List — `up.railway.app` is on
 * it — so `web.up.railway.app` and `api.up.railway.app` are different *sites*,
 * and the SameSite=Lax session cookie would not be sent with any request the
 * page makes. Login would appear to succeed and every call after it would 401.
 * Proxying removes the problem rather than weakening the cookie to
 * SameSite=None to work around it — and removes the CORS config along with it.
 *
 * This holds locally too: with PANELIST_API_ORIGIN unset the route handler
 * still forwards, defaulting to http://127.0.0.1:8000. There is one path in
 * every environment, so there is one path to get wrong.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    // Relative by default, so the browser only ever calls this server and
    // app/api/[...path]/route.ts forwards from there — same origin in every
    // environment, which is what keeps the session cookie working and makes
    // CORS irrelevant. Set NEXT_PUBLIC_API_URL to an absolute URL only to
    // deliberately bypass the proxy and call an API directly.
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "/api",
  },
  // A production `next build` writes the same .next/ the dev server is serving
  // from, which replaces the chunks dev has already handed the browser and
  // leaves it throwing "Cannot find module './xxx.js'". Setting NEXT_DIST_DIR
  // lets a verification build run against its own directory while dev keeps
  // serving — e.g. `NEXT_DIST_DIR=.next-build npm run build`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
