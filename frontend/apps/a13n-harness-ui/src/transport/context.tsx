import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { result, type Transport, type Schema } from "./client";

export const GuestAccessContext = createContext<Schema<"ShareAccess"> | null>(
  null,
);
export const useGuestAccess = () => useContext(GuestAccessContext);

export const TransportContext = createContext<Transport | null>(null);
export function useTransport() {
  const transport = useContext(TransportContext);
  if (!transport) throw new Error("Authenticated transport is missing.");
  return transport;
}
export function useStatus(refetchInterval?: number) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["status"],
    enabled: !shareId,
    refetchInterval,
    queryFn: ({ signal }) => result(client.GET("/api/status", { signal })),
  });
}
export function useUpdateNotices() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["update-notices"],
    enabled: !shareId,
    staleTime: 10 * 60 * 1000,
    refetchInterval: 60 * 60 * 1000,
    queryFn: ({ signal }) =>
      result(client.GET("/api/update-notices", { signal })),
  });
}
export function useSources(enabled = true) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["sources"],
    enabled: !shareId && enabled,
    queryFn: ({ signal }) =>
      result(client.GET("/api/configuration/sources", { signal })),
  });
}
export function useWebProviders(enabled = true) {
  const { client, shareId } = useTransport();
  const sources = useSources();
  return useQuery({
    queryKey: ["web-providers", sources.data?.generation_digest],
    enabled: !shareId && enabled,
    queryFn: ({ signal }) =>
      result(client.GET("/api/web-providers", { signal })),
  });
}
export function useSelectors() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["selectors"],
    enabled: !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/selectors", { signal })),
  });
}
export function useProjects() {
  const { client, shareId } = useTransport();
  const setup = useSetup();
  const cwd = setup.data?.suggested_project_path;
  return useQuery({
    queryKey: ["projects"],
    enabled: !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/projects", { signal })),
    // Default presentation only; explicit browser ordering still takes precedence.
    select: (projects) =>
      [...projects].sort(
        (a, b) => Number(b.roots[0] === cwd) - Number(a.roots[0] === cwd),
      ),
  });
}
export function useSetup() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["setup"],
    enabled: !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/setup", { signal })),
  });
}
