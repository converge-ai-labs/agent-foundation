import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";

export type TraceBackendType = NonNullable<Schema["TraceBackend"]["type"]>;

/** The backend trace queries read; its `type` is null when trace query is not configured. */
export function useTraceBackend() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["trace-backend", workspace.id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/trace-backend", {
          signal,
        })
        .then(data),
  });
}

export function backendName(type: TraceBackendType) {
  return type === "langfuse" ? "Langfuse" : "Logfire";
}
