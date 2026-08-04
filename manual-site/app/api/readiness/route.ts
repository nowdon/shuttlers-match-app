export function GET() {
  const token = process.env.MANUAL_PREVIEW_READINESS_TOKEN;

  if (!token) {
    return new Response(null, { status: 404 });
  }

  return new Response(token, {
    headers: {
      "cache-control": "no-store",
      "content-type": "text/plain; charset=utf-8",
    },
  });
}
