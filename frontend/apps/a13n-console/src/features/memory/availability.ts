import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { credentialMode } from "../../shared/provider-authentication";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";

/**
 * A provider can serve content only while it is enabled and its declared
 * authentication is satisfied; a Provider that forbids credentials needs none.
 */
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

/** The installed Memory definitions one scope can select from. */
export function useMemoryProviderDefinitions(
  scope: MemoryProviderScope,
  enabled = true,
) {
  const client = useClient();
  return useQuery({
    queryKey: ["memory-provider-types", scope.kind, scope.id],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/memory-provider-types", {
          signal,
          headers:
            scope.kind === "workspace" ? workspaceHeaders(scope.id) : undefined,
        })
        .then(data),
  });
}

/** The workspace catalog every agent-facing Memory selector reads. */
export function useWorkspaceMemoryProviderDefinitions() {
  const { workspace, can } = useWorkspace();
  return useMemoryProviderDefinitions(
    { kind: "workspace", id: workspace.id },
    can("memory_provider.read"),
  );
}
