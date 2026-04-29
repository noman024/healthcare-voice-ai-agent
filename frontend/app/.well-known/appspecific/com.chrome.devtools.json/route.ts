/**
 * Chrome DevTools probes this URL; absent route shows 404 in dev logs (harmless).
 */
export function GET() {
  return new Response(null, { status: 204 });
}
