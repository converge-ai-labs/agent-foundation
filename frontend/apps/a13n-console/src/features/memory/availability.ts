import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { credentialMode } from "../../shared/provider-authentication";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { memoryProviderApi } from "./providers-api";

export function eligibleMemoryProvider(
  provider: Schema["MemoryProvider"],
  definitions: Schema["MemoryProviderMetadata"][],
) {
  const definition = definitions.find((item) => item.type === provider.type);
  if (!provider.enabled || !definition) return false;
  const mode = credentialMode(definition, provider.configuration);
  return (
    mode === "optional" ||
    (mode === "required"
      ? provider.credential_configured
      : !provider.credential_configured)
  );
}

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

export function useMemoryProviderDefinitions() {
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const canRead = can("memory_provider.read");
  return useQuery({
    queryKey: ["memory-provider-types", "workspace", workspace.id],
    enabled: canRead,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/memory-provider-types", {
          signal,
          headers: workspaceHeaders(workspace.id),
        })
        .then(data),
  });
}
