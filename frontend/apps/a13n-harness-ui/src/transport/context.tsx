import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { result, type Transport } from "./client";

export const TransportContext = createContext<Transport | null>(null);
export function useTransport() {
  const transport = useContext(TransportContext);
  if (!transport) throw new Error("Authenticated transport is missing.");
  return transport;
}
export function useStatus(refetchInterval?: number) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["status"],
    refetchInterval,
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
  const setup = useSetup();
  const cwd = setup.data?.suggested_project_path;
  return useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => result(client.GET("/api/projects", { signal })),
    // Default presentation only; explicit browser ordering still takes precedence.
    select: (projects) =>
      [...projects].sort(
        (a, b) => Number(b.roots[0] === cwd) - Number(a.roots[0] === cwd),
      ),
  });
}
export function useSetup() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["setup"],
    queryFn: ({ signal }) => result(client.GET("/api/setup", { signal })),
  });
}
