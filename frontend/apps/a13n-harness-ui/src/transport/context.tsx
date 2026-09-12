import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { result, type Transport } from "./client";

export const TransportContext = createContext<Transport | null>(null);
export function useTransport() {
  const transport = useContext(TransportContext);
  if (!transport) throw new Error("Authenticated transport is missing.");
  return transport;
}
export function useStatus() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["status"],
    queryFn: ({ signal }) => result(client.GET("/api/status", { signal })),
  });
}
export function useSources() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["sources"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/configuration/sources", { signal })),
  });
}
export function useSelectors() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["selectors"],
    queryFn: ({ signal }) => result(client.GET("/api/selectors", { signal })),
  });
}
export function useProjects() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => result(client.GET("/api/projects", { signal })),
  });
}
export function useSetup() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["setup"],
    queryFn: ({ signal }) => result(client.GET("/api/setup", { signal })),
  });
}
