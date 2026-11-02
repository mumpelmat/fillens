import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
const proxy = async (
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) => {
  const { path } = await context.params;
  if (path.some((p) => !/^[a-zA-Z0-9_-]+$/.test(p)))
    return Response.json({ detail: "Ungültiger Pfad." }, { status: 400 });
  if (
    !["GET", "HEAD"].includes(request.method) &&
    request.headers.get("sec-fetch-site") === "cross-site"
  ) {
    return Response.json(
      { detail: "Anfrage von fremder Website abgelehnt." },
      { status: 403 },
    );
  }
  const headers = new Headers();
  for (const key of [
    "cookie",
    "content-type",
    "x-csrf-token",
    "sec-fetch-site",
  ]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (!["GET", "HEAD"].includes(request.method)) {
      const upload =
        request.method === "POST" && path.join("/") === "training/samples";
      const limit = upload
        ? Number(process.env.MAX_TRAINING_UPLOAD_BYTES || 15728640) + 65536
        : 65536;
      if (Number(request.headers.get("content-length") || "0") > limit)
        return Response.json({ detail: "Anfrage zu groß." }, { status: 413 });
      const reader = request.body?.getReader();
      const chunks: Buffer[] = [];
      let bytes = 0;
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > limit) {
            await reader.cancel();
            return Response.json(
              { detail: "Anfrage zu groß." },
              { status: 413 },
            );
          }
          chunks.push(Buffer.from(value));
        }
      }
      body = new Uint8Array(Buffer.concat(chunks));
    }
    const origin = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
    const response = await fetch(
      `${origin}/api/${path.join("/")}${request.nextUrl.search}`,
      {
        method: request.method,
        headers,
        body,
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(30000),
      },
    );
    const output = new Headers({ "Cache-Control": "no-store" });
    for (const key of ["content-type", "content-length", "retry-after"]) {
      const value = response.headers.get(key);
      if (value) output.set(key, value);
    }
    for (const value of response.headers.getSetCookie())
      output.append("set-cookie", value);
    return new Response(response.body, {
      status: response.status,
      headers: output,
    });
  } catch {
    return Response.json(
      {
        detail: "API derzeit nicht erreichbar. Bitte später erneut versuchen.",
      },
      { status: 502 },
    );
  }
};
export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
