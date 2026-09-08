import { ApiError, data, type components } from "@converge.ai/a13n";
export { data };
export type Schema = components["schemas"];
export function representation<T>(result: { data?: T; response: Response }) {
  return {
    value: data(result),
    etag: result.response.headers.get("ETag") ?? undefined,
  };
}
export function isUnauthorized(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}
export function commandHeaders(
  workspaceId: string | undefined,
  key: string,
  etag?: string,
) {
  return {
    ...(workspaceId ? workspaceHeaders(workspaceId) : {}),
    "Idempotency-Key": key,
    ...(etag ? { "If-Match": etag } : {}),
  };
}
export function workspaceHeaders(workspaceId: string) {
  return { "X-A13N-Workspace-ID": workspaceId };
}
/** Picker collections traverse the canonical cursor; table views page explicitly. */
export async function allPages<T>(
  read: (
    cursor?: string,
  ) => Promise<{ items: T[]; next_cursor?: string | null }>,
): Promise<T[]> {
  const items: T[] = [],
    seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await read(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor))
      throw new Error("The Service returned a repeated pagination cursor.");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}
