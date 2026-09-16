import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { connectorApi } from "../connectors/api";
import { useMCPServers } from "../mcp/catalog";

export function useConnectionDirectory(search: string) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace();
  const providers = useQuery({
    queryKey: ["connector-providers", "workspace", workspace.id, "picker"],
    enabled: can("connector_provider.read") && can("connection.manage"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        connectorApi(client, { kind: "workspace", id: workspace.id }).providers(
          signal,
          cursor,
        ),
      ),
  });
  const mcpServers = useMCPServers(search, can("connection.manage"));
  const active =
    providers.data?.filter((provider) => provider.status === "active") ?? [];
  const key = (provider: Schema["ConnectorProvider"]) => [
    "connector-directory",
    workspace.id,
    provider.id,
    provider.credential_generation,
    search,
  ];
  const read = (
    provider: Schema["ConnectorProvider"],
    options: { cursor?: string; refresh?: boolean } = {},
    signal?: AbortSignal,
  ) =>
    client.http
      .POST(
        "/api/v1/connector-providers/{connector_provider_id}/discover-connectors",
        {
          params: {
            path: { connector_provider_id: provider.id },
            query: { query: search, limit: 50, ...options },
          },
          signal,
        },
      )
      .then(data);
  const queries = useQueries({
    queries: active.map((provider) => ({
      queryKey: key(provider),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        read(provider, {}, signal),
      staleTime: 30_000,
    })),
  });
  const load = useMutation({
    mutationFn: async (refresh: boolean) => {
      const outcomes = await Promise.allSettled(
        active.map(async (provider, index) => {
          const cursor = queries[index].data?.next_cursor;
          if (!refresh && !cursor) return;
          const page = await read(
            provider,
            refresh ? { refresh: true } : { cursor: cursor ?? undefined },
          );
          cache.setQueryData<Schema["ConnectorCollection"]>(
            key(provider),
            (current) => {
              if (refresh || !current) return page;
              if (current.next_cursor !== cursor) return current;
              return { ...page, items: [...current.items, ...page.items] };
            },
          );
        }),
      );
      const failed = outcomes.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
    },
  });
  return {
    entries: queries.flatMap((query, index) =>
      (query.data?.items ?? []).map((connector) => ({
        connector,
        provider: active[index],
      })),
    ),
    mcpServers: mcpServers.data ?? [],
    providers: active,
    pending:
      providers.isLoading ||
      mcpServers.isLoading ||
      queries.some((query) => query.isLoading),
    error:
      providers.error ??
      mcpServers.error ??
      queries.find((query) => query.error)?.error ??
      load.error,
    hasMore: queries.some((query) => !!query.data?.next_cursor),
    loadingMore: load.isPending,
    loadMore: () => load.mutate(false),
    refresh: () => load.mutate(true),
  };
}
