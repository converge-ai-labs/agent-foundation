import { ApiError, ProtocolError, type Client } from "../../service-client";
import { data, workspaceHeaders, type Schema } from "../../shared/api";

type DisplayRead =
  | { available: false; items: Schema["ItemResource"][] }
  | ({ available: true } & Schema["ItemCollection"]);

export async function readDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
): Promise<DisplayRead> {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try {
      let snapshot: Schema["ItemCollection"] | undefined;
      let cursor: string | undefined;
      const seen = new Set<string>();
      const items: Schema["ItemResource"][] = [];
      do {
        const page = data(
          await client.http.GET("/api/v1/runs/{run_id}/items", {
            params: { path: { run_id: runId }, query: { cursor, limit: 100 } },
            headers: workspaceHeaders(workspaceId),
            signal,
          }),
        );
        if (
          snapshot &&
          (page.snapshot_version !== snapshot.snapshot_version ||
            page.projection_cursor !== snapshot.projection_cursor ||
            page.complete !== snapshot.complete ||
            page.finalized !== snapshot.finalized ||
            page.incomplete_reason !== snapshot.incomplete_reason)
        )
          throw new ProtocolError(
            "Run display pages have inconsistent coverage.",
          );
        snapshot = page;
        items.push(...page.items);
        cursor = page.next_cursor ?? undefined;
        if (cursor && seen.has(cursor))
          throw new ProtocolError("Run display pagination repeated a cursor.");
        if (cursor) seen.add(cursor);
      } while (cursor);
      return { ...snapshot, available: true, items, next_cursor: null };
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "items_unavailable")
          return { available: false, items: [] };
        // A replacement invalidates every page already read, including its cursor.
        if (error.code === "items_snapshot_changed" && attempt < 2) continue;
      }
      throw error;
    }
  }
}
