/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
  // A production `next build` writes the same .next/ the dev server is serving
  // from, which replaces the chunks dev has already handed the browser and
  // leaves it throwing "Cannot find module './xxx.js'". Setting NEXT_DIST_DIR
  // lets a verification build run against its own directory while dev keeps
  // serving — e.g. `NEXT_DIST_DIR=.next-build npm run build`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
