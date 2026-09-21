import { ApiError, type Client } from "../../service-client";
import { data, workspaceHeaders, type Schema } from "../../shared/api";

export type DisplayRead =
  | {
      available: false;
      items: Schema["ItemResource"][];
      recovery_exhausted?: boolean;
    }
  | ({ available: true } & Schema["ItemCollection"]);

/** Read one page, newest first on the wire and chronological for presentation. */
export async function readDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
  cursor?: string,
): Promise<DisplayRead> {
  try {
    const page = data(
      await client.http.GET("/api/v1/runs/{run_id}/items", {
        params: {
          path: { run_id: runId },
          query: { cursor, limit: 50, order: "desc" },
        },
        headers: workspaceHeaders(workspaceId),
        signal,
      }),
    );
    return { ...page, available: true, items: [...page.items].reverse() };
  } catch (error) {
    if (error instanceof ApiError && error.code === "items_unavailable")
      return {
        available: false,
        items: [],
        recovery_exhausted: error.details.recovery_exhausted === true,
      };
    throw error;
  }
}
