import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

const PRODUCT = "a13n";

/** Destination names, keyed by the first segment of the in-workspace path. */
const destinations: Record<string, string> = {
  agents: "Agents",
  "application-accounts": "Application accounts",
  bots: "Bots",
  configuration: "Configuration assistant",
  "configuration-threads": "Configuration assistant",
  connections: "Connections",
  environments: "Environments",
  memories: "Memories",
  models: "Models",
  schedules: "Schedules",
  sessions: "Sessions",
  settings: "Settings",
  skills: "Skills",
  traces: "Traces",
  usage: "Usage",
};

/**
 * The name of the destination a path leads to, before translation. Detail
 * routes keep their collection's name; unknown paths have no name and leave
 * the document title to the workspace alone.
 */
export function pageName(pathname: string): string | undefined {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] === "organization") return "Organization settings";
  if (segments[0] === "settings") return "Personal settings";
  const scoped = segments[0] === "workspace" ? segments.slice(2) : segments;
  if (!scoped.length) return undefined;
  return destinations[scoped[0]];
}

/**
 * Every route names itself in the browser: the destination, the workspace it
 * belongs to, then the product.
 */
export function usePageTitle(workspaceName?: string) {
  const { pathname } = useLocation();
  const { t, i18n } = useTranslation();
  useEffect(() => {
    const name = pageName(pathname);
    document.title = [name && t(name), workspaceName, PRODUCT]
      .filter(Boolean)
      .join(" · ");
  }, [pathname, workspaceName, t, i18n.resolvedLanguage]);
}
