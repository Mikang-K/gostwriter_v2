export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: Request) {
  const requestId = crypto.randomUUID();
  const url = new URL(request.url);
  const origin = process.env.API_BASE_URL || "http://127.0.0.1:8000";
  const headers = new Headers();
  for (const key of ["content-type", "cookie", "origin"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  let body: Uint8Array | undefined;
  if (!["GET", "HEAD"].includes(request.method) && request.body) {
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.length;
      if (size > 65536) {
        await reader.cancel();
        return Response.json({ error: { code: "BODY_TOO_LARGE", requestId } }, { status: 413 });
      }
      chunks.push(part.value);
    }
    body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  }
  try {
    const response = await fetch(new URL(url.pathname + url.search, origin), {
      method: request.method, headers, body: body as BodyInit | undefined,
      signal: request.signal, cache: "no-store", redirect: "manual",
    });
    const output = new Headers();
    for (const key of ["content-type", "cache-control", "x-request-id", "x-accel-buffering"]) {
      const value = response.headers.get(key);
      if (value) output.set(key, value);
    }
    for (const cookie of response.headers.getSetCookie()) output.append("set-cookie", cookie);
    return new Response(response.body, { status: response.status, headers: output });
  } catch {
    return Response.json({ error: { code: "API_UNAVAILABLE", requestId } }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
