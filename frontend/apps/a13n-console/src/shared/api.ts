import {
  ApiError,
  data,
  type Client,
  type components,
} from "../service-client";
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
/**
 * The Service's strong ETag of a row as the reader saw it, for rows read from a
 * collection, whose response carries no per-item ETag.
 */
export function rowTag(row: { id: string; version: number }) {
  return `"${row.id}:${row.version}"`;
}
export function ifMatch(etag: string | undefined) {
  return etag ? { "If-Match": etag } : {};
}
/** Execution commands (new thread, message, fork, resume, upload) replay by request key. */
export function commandHeaders(key: string) {
  return { "Idempotency-Key": key };
}
/** Stage one file for a resource that takes its `upload_id`; the key replays a lost acknowledgement. */
export async function uploadFile(
  client: Client,
  workspaceId: string,
  file: File,
  key: string,
): Promise<Schema["Upload"]> {
  return data(
    await client.workspace(workspaceId).POST("/api/v1/uploads", {
      params: {
        header: commandHeaders(key),
      },
      body: { file },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file);
        return form;
      },
    }),
  );
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
