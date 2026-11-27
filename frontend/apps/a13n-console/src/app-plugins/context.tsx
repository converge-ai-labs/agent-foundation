import { createContext, useContext } from "react";
import { useClient } from "../auth/context";
import { useAccess } from "../layout/workspace";
import { useTranslation } from "react-i18next";
import type { InstalledApp, LoadedApp } from "./types";

const emptyCatalog: InstalledApp[] = [];

export const Registry = createContext<{
  apps: LoadedApp[];
  catalog: InstalledApp[];
  pending: boolean;
  error: unknown;
}>({ apps: [], catalog: emptyCatalog, pending: false, error: null });

export function useInstalledApps() {
  return useContext(Registry).catalog;
}

export function useAppLabel(appId: string) {
  return useInstalledApps().find((app) => app.app_id === appId)?.name ?? appId;
}

export const AppContext = createContext<string | null>(null);

/** Scope and requests are taken from the currently mounted host providers. */
export function useApp() {
  const { t } = useTranslation();
  const appId = useContext(AppContext);
  const client = useClient();
  const { organization, workspace } = useAccess();
  if (!appId) throw new Error(t("useApp requires a mounted App contribution."));
  return {
    appId,
    organizationId: organization.id,
    workspaceId: workspace?.id,
    queryKey: (...parts: readonly unknown[]) =>
      ["app", appId, organization.id, workspace?.id, ...parts] as const,
    request: (path: string, init: RequestInit = {}) => {
      if (!workspace)
        throw new Error(
          t("App business requests require a selected workspace."),
        );
      if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
        throw new TypeError(
          t("App requests use a relative path beginning with /."),
        );
      const namespace = `/api/v1/apps/${appId}/`;
      const url = new URL(
        `${namespace}${path.slice(1)}`,
        window.location.origin,
      );
      if (!url.pathname.startsWith(namespace))
        throw new TypeError(
          t("App requests must stay within their App namespace."),
        );
      const headers = new Headers(init.headers);
      headers.set("X-Workspace-ID", workspace.id);
      return client.request(`${url.pathname}${url.search}`, {
        ...init,
        headers,
      });
    },
  };
}
