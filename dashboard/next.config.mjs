/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    // Relative by default, so the browser only ever calls this server and
    // app/api/[...path]/route.ts forwards from there. Same origin in every
    // environment keeps the session cookie working and makes CORS irrelevant.
    // Set NEXT_PUBLIC_API_URL to an absolute URL only to bypass the proxy.
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "/api",
  },
  // A production `next build` otherwise overwrites the .next/ the dev server
  // is serving from, leaving the browser on chunks that no longer exist. Run
  // a verification build as `NEXT_DIST_DIR=.next-build npm run build`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
