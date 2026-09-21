/** Response helpers shared by every fake Service route. */

export interface RouteMatch {
  params: Record<string, string>;
  url: URL;
  request: Request;
}
export type Handler = (match: RouteMatch) => Response | Promise<Response>;
export interface Route {
  method: string;
  pattern: string;
  handle: Handler;
}

const headers = () => ({ "X-Request-ID": `req_preview_${Date.now()}` });

export function json(body: unknown, init: ResponseInit = {}): Response {
  return Response.json(body, {
    ...init,
    headers: { ...headers(), ...init.headers },
  });
}

export function page<T>(items: T[], nextCursor: string | null = null) {
  return json({ items, next_cursor: nextCursor });
}

export function noContent(): Response {
  return new Response(null, { status: 204, headers: headers() });
}

export function fail(
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {},
): Response {
  return json(
    {
      error: {
        code,
        message,
        details,
        request_id: `req_preview_${Date.now()}`,
      },
    },
    { status },
  );
}

export const notFound = (what: string) =>
  fail(404, "resource_not_found", `${what} was not found.`);

export const conflict = (message: string) =>
  fail(409, "precondition_failed", message);

/** Match `/api/v1/runs/{run_id}/items` style patterns segment by segment. */
export function matchPath(
  pattern: string,
  pathname: string,
): Record<string, string> | null {
  const expected = pattern.split("/");
  const actual = pathname.split("/");
  if (expected.length !== actual.length) return null;
  const params: Record<string, string> = {};
  for (const [index, segment] of expected.entries()) {
    const value = actual[index]!;
    if (segment.startsWith("{") && segment.endsWith("}")) {
      if (!value) return null;
      params[segment.slice(1, -1)] = decodeURIComponent(value);
    } else if (segment !== value) return null;
  }
  return params;
}

export async function body(request: Request): Promise<Record<string, unknown>> {
  try {
    const value: unknown = await request.clone().json();
    return typeof value === "object" && value !== null
      ? (value as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

export function integer(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : fallback;
}
