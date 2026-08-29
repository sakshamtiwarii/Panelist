/**
 * Same-origin proxy to the solver API, so the browser only ever talks to this
 * server.
 *
 * Platform subdomains are on the Public Suffix List (`up.railway.app` is), so
 * `web.up.railway.app` and `api.up.railway.app` are different sites and the
 * SameSite=Lax session cookie would not be sent with the page's own requests —
 * login would appear to succeed and every call after it would 401.
 *
 * A route handler rather than a `rewrites()` entry: Next evaluates rewrites at
 * build time and bakes the destination into routes-manifest.json, so an API
 * address supplied as a runtime variable would ship a build pointing elsewhere.
 * Resolving per request cannot drift from the environment it runs in.
 */

const ORIGIN = (process.env.PANELIST_API_ORIGIN || "http://127.0.0.1:8000")
  .replace(/\/+$/, "");

// Several platforms hand over a bare hostname (Render's `host`), which `fetch`
// rejects without a scheme.
const BASE = /^https?:\/\//.test(ORIGIN) ? ORIGIN : `https://${ORIGIN}`;

// Hop-by-hop and body-framing headers describe this connection, not the
// forwarded one; passing them through corrupts the response.
const STRIP = new Set([
  "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
  "proxy-authorization", "proxy-authenticate", "te", "trailer",
  "content-length", "content-encoding",
]);

async function proxy(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const { search } = new URL(req.url);
  const target = `${BASE}/${path.join("/")}${search}`;

  const headers = new Headers();
  req.headers.forEach((v, k) => {
    if (!STRIP.has(k.toLowerCase())) headers.set(k, v);
  });

  let res: Response;
  try {
    res = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD"
        ? undefined
        : await req.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    // 502 rather than a thrown 500, so the console can tell "the solver is
    // down" apart from "the solver said no".
    return Response.json(
      { detail: `Cannot reach the scheduler API at ${BASE}.` },
      { status: 502 },
    );
  }

  const out = new Headers();
  res.headers.forEach((v, k) => {
    if (!STRIP.has(k.toLowerCase()) && k.toLowerCase() !== "set-cookie") {
      out.set(k, v);
    }
  });
  // Set-Cookie must survive as separate headers — a collapsed comma-joined
  // value is not a cookie any browser will store.
  for (const cookie of res.headers.getSetCookie?.() ?? []) {
    out.append("set-cookie", cookie);
  }

  return new Response(res.body, { status: res.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
export const HEAD = proxy;

// The session cookie makes every one of these request-specific.
export const dynamic = "force-dynamic";
