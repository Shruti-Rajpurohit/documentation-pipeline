import { NextRequest, NextResponse } from "next/server";

type RouteContext = { params: Promise<{ path: string[] }> };
const ALLOWED_METHODS = new Set(["GET", "POST"]);
const SESSION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isAllowedRoute(method: string, path: string[]) {
  if (method === "GET") {
    return path.length === 0 || (path.length === 1 && (path[0] === "me" || SESSION_ID_PATTERN.test(path[0])));
  }
  if (method === "POST") {
    return path.length === 0 || (path.length === 2 && SESSION_ID_PATTERN.test(path[0]) && ["edit", "approve", "reject"].includes(path[1]));
  }
  return false;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  if (!path.length || path[0] !== "sessions" || !ALLOWED_METHODS.has(request.method) || !isAllowedRoute(request.method, path.slice(1))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  if (request.method === "POST" && request.headers.get("origin") !== request.nextUrl.origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected" }, { status: 403 });
  }

  const backend = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";
  const userId = process.env.REVIEW_USER_ID;
  const proxySecret = process.env.AUTH_PROXY_SECRET;
  if (!userId || !proxySecret) {
    const missingConfiguration = [
      !userId && "REVIEW_USER_ID",
      !proxySecret && "AUTH_PROXY_SECRET",
    ].filter((value): value is string => value !== false);
    return NextResponse.json(
      { detail: `Frontend server configuration is missing: ${missingConfiguration.join(", ")}` },
      { status: 503 },
    );
  }

  const target = new URL(`${backend.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;
  const headers = new Headers({
    "X-Authenticated-User-ID": userId,
    "X-Auth-Proxy-Secret": proxySecret,
    Accept: "application/json",
  });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > 1_000_000) {
    return NextResponse.json({ detail: "Request body is too large" }, { status: 413 });
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(125_000),
    });
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Documentation API is unavailable" }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;