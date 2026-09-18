import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages } from "../../shared/api";
import { memoryProviderApi } from "./providers-api";

/** Shared by discovery and selectors; visibility is not content authorization. */
export function useMemoryProviders() {
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const canRead = can("memory_provider.read");
  const providers = useQuery({
    queryKey: ["memory-providers", "workspace", workspace.id, "choices"],
    enabled: canRead,
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        memoryProviderApi(client, {
          kind: "workspace",
          id: workspace.id,
        }).providers(signal, cursor),
      ),
  });
  // A denied or failed catalog is unknown, not proof of an unconfigured backend.
  // Keep deep links usable for callers with subject-specific memory access.
  const visible =
    !canRead || providers.isError || (providers.data?.length ?? 0) > 0;
  return { providers, visible };
}

/** A provider can serve content only while it is enabled and configured. */
export function memoryProviderUsable(provider: {
  enabled?: boolean | null;
  credential_configured?: boolean | null;
}) {
  return !!provider.enabled && !!provider.credential_configured;
}
