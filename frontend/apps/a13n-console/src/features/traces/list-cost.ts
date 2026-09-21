import { ApiError } from "../../service-client";
import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { observationCost } from "./cost";

/** Bound background reads; an incomplete trace is never presented as a total. */
export function useListCosts(
  workspace: string,
  traces: readonly Schema["Trace"][],
) {
  const client = useClient();
  return useQuery({
    queryKey: [
      "trace-list-costs",
      workspace,
      traces.map((trace) => [
        trace.id,
        trace.root.cost_usd,
        trace.root.ended_at,
      ]),
    ],
    enabled: traces.length > 0,
    retry: false,
    queryFn: async ({ signal }) => {
      const results: Record<string, string | null> = {};
      let next = 0;
      let accessError: ApiError | undefined;
      async function worker() {
        while (next < traces.length && !signal.aborted && !accessError) {
          const trace = traces[next++];
          const observations = new Map<string, Schema["Observation"]>();
          const cursors = new Set<string>();
          let cursor: string | undefined;
          results[trace.id] = null;
          try {
            // At most 2,000 observations per trace. Larger traces stay unknown.
            for (let page = 0; page < 20 && !accessError; page++) {
              signal.throwIfAborted();
              const collection = data(
                await client.http.GET(
                  "/api/v1/workspaces/{workspace}/traces/{trace_id}/observations",
                  {
                    params: {
                      path: { workspace, trace_id: trace.id },
                      query: { view: "compact", limit: 100, cursor },
                    },
                    signal,
                  },
                ),
              );
              for (const observation of collection.items)
                observations.set(observation.id, observation);
              if (collection.next_cursor === null) {
                if (!observations.has(trace.root.id))
                  observations.set(trace.root.id, trace.root);
                results[trace.id] = observationCost([
                  ...observations.values(),
                ]).total;
                break;
              }
              if (cursors.has(collection.next_cursor)) break;
              cursors.add(collection.next_cursor);
              cursor = collection.next_cursor;
            }
          } catch (error) {
            signal.throwIfAborted();
            if (
              error instanceof ApiError &&
              [401, 403, 404].includes(error.status)
            ) {
              accessError = error;
              throw error;
            }
            // Failed reads leave a missing value, never a root-only subtotal.
          }
        }
      }
      await Promise.all(
        Array.from({ length: Math.min(4, traces.length) }, () => worker()),
      );
      signal.throwIfAborted();
      return results;
    },
  });
}
