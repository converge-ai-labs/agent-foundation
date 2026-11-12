import { useQuery } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";

export function useConnectors() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["connectors", "instances"],
    enabled: !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/connectors", { signal })),
    refetchInterval: 5000,
  });
}

export function useConnectorCatalog() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["connectors", "catalog"],
    enabled: !shareId,
    queryFn: ({ signal }) =>
      result(client.GET("/api/connectors/catalog", { signal })),
  });
}
