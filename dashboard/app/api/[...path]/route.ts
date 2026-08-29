/**
 * Same-origin proxy to the solver API.
 *
 * The browser only ever talks to this server, which matters because platform
 * subdomains are on the Public Suffix List — `up.railway.app` is on it — so
 * `web.up.railway.app` and `api.up.railway.app` are different *sites*, and the
 * SameSite=Lax session cookie would not be sent with the page's own requests.
 * Login would appear to succeed and every call after it would 401. Sharing an
 * origin removes that, and removes the CORS configuration with it.
 *
 * Deliberately a route handler rather than a `rewrites()` entry. Next evaluates
 * rewrites at BUILD time and bakes the destination into routes-manifest.json,
 * so a platform that supplies the API's address as a runtime variable silently
 * ships a build pointing somewhere else — during development of this file that
 * meant proxying to a stranger's host that happened to own the placeholder
 * name. Resolving per request cannot drift from the environment it runs in.
 */

const ORIGIN = (process.env.PANELIST_API_ORIGIN || "http://127.0.0.1:8000")
  .replace(/\/+$/, "");

// A bare hostname is what several platforms hand over (Render's `host`
// property); without a scheme `fetch` rejects it outright.
const BASE = /^https?:\/\//.test(ORIGIN) ? ORIGIN : `https://${ORIGIN}`;

// Hop-by-hop and body-framing headers describe THIS connection, not the
// forwarded one — passing them through corrupts the response.
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
    // The API is unreachable. 502 rather than a thrown 500, so the console can
    // tell "the solver is down" apart from "the solver said no".
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
  // Set-Cookie must survive as separate headers: the session cookie is the
  // whole point of proxying, and a collapsed comma-joined value is not a
  // cookie any browser will store.
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
