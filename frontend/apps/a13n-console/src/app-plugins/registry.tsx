import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useClient } from "../auth/context";
import { useAccess } from "../layout/workspace";
import { data } from "../shared/api";
import {
  validateContribution,
  type AppContribution,
  type InstalledApp,
  type LoadedApp,
} from "./types";

export const catalogKey = (organizationId: string) =>
  ["app-plugins", organizationId] as const;
export function useAppCatalog() {
  const client = useClient();
  const { organization } = useAccess();
  return useQuery({
    queryKey: catalogKey(organization.id),
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/app-plugins", {
          params: { path: { organization_id: organization.id } },
          signal,
        })
        .then(data),
  });
}

export async function loadApp(
  app: InstalledApp,
  styles: HTMLLinkElement[],
): Promise<LoadedApp> {
  try {
    const frontend = app.frontend!;
    if (frontend.bridge !== 1)
      throw new Error("This App requires an unsupported Console bridge.");
    await Promise.all(
      frontend.styles.map(
        (href) =>
          new Promise<void>((resolve, reject) => {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = href;
            link.onload = () => resolve();
            link.onerror = () =>
              reject(new Error(`Could not load App stylesheet: ${href}`));
            styles.push(link);
            document.head.append(link);
          }),
      ),
    );
    const module = (await import(/* @vite-ignore */ frontend.entry)) as {
      default: AppContribution;
    };
    return { app, contribution: validateContribution(module.default) };
  } catch (error) {
    return {
      app,
      error: error instanceof Error ? error : new Error(String(error)),
    };
  }
}
const Registry = createContext<{
  apps: LoadedApp[];
  pending: boolean;
  error: unknown;
}>({ apps: [], pending: false, error: null });

export function AppPluginsProvider({ children }: { children: ReactNode }) {
  const { organization } = useAccess();
  const catalog = useAppCatalog();
  const [loaded, setLoaded] = useState<{
    inventory: typeof catalog.data;
    apps: LoadedApp[];
  }>();
  useEffect(() => {
    const inventory = catalog.data;
    if (!inventory) return;
    let active = true;
    const styles: HTMLLinkElement[] = [];
    void Promise.all(
      inventory.items
        .filter((app) => app.enabled && app.frontend)
        .map((app) => loadApp(app, styles)),
    ).then((apps) => {
      if (active) setLoaded({ inventory, apps });
    });
    return () => {
      active = false;
      styles.forEach((link) => link.remove());
    };
  }, [catalog.data, organization.id]);
  // Never expose a previous organization's contributions while its replacement loads.
  const current = loaded?.inventory === catalog.data;
  return (
    <Registry.Provider
      value={{
        apps: current ? (loaded?.apps ?? []) : [],
        pending: catalog.isPending || (!!catalog.data && !current),
        error: catalog.error,
      }}
    >
      {children}
    </Registry.Provider>
  );
}
export function useAppPlugins() {
  return useContext(Registry);
}
