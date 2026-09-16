import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { allPages, data } from "../../shared/api";

export function useMCPServers(search = "", enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: ["mcp-servers", search],
    enabled,
    staleTime: 300_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/mcp-servers", {
            params: { query: { query: search, limit: 200, cursor } },
            signal,
          })
          .then(data),
      ),
  });
}
