import { createContext, useContext } from "react";
import { useClient } from "../auth/context";
import { useAccess } from "../layout/workspace";
export const AppContext = createContext<string | null>(null);

/** Scope and requests are taken from the currently mounted host providers. */
export function useApp() {
  const appId = useContext(AppContext);
  const client = useClient();
  const { organization, workspace } = useAccess();
  if (!appId) throw new Error("useApp requires a mounted App contribution.");
  return {
    appId,
    organizationId: organization.id,
    workspaceId: workspace?.id,
    queryKey: (...parts: readonly unknown[]) =>
      ["app", appId, organization.id, workspace?.id, ...parts] as const,
    request: (path: string, init: RequestInit = {}) => {
      if (!workspace)
        throw new Error("App business requests require a selected workspace.");
      if (!path.startsWith("/") || path.startsWith("//"))
        throw new TypeError(
          "App requests use a relative path beginning with /.",
        );
      const headers = new Headers(init.headers);
      headers.set("X-Workspace-ID", workspace.id);
      return client.request(`/api/v1/apps/${appId}${path}`, {
        ...init,
        headers,
      });
    },
  };
}
