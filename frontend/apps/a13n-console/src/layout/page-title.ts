import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import {
  resolveSection,
  settingsSectionLabels,
  type SettingsScope,
} from "../features/settings/navigation";

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
 * Settings sections are addressable pages, so the title names the section the
 * reader is on rather than repeating "Settings" for all of them.
 */
export function sectionName(pathname: string): string | undefined {
  const segments = pathname.split("/").filter(Boolean);
  let scope: SettingsScope | undefined;
  let rest: string[] = [];
  if (segments[0] === "settings") {
    scope = "personal";
    rest = segments.slice(1);
  } else if (segments[0] === "organization" && segments[1] === "settings") {
    scope = "organization";
    rest = segments.slice(2);
  } else if (segments[0] === "workspace" && segments[2] === "settings") {
    scope = "workspace";
    rest = segments.slice(3);
  }
  if (!scope) return undefined;
  const value = resolveSection(scope, rest[0]);
  return value ? settingsSectionLabels[scope][value] : undefined;
}

/**
 * Every route names itself in the browser: the section, the destination, the
 * workspace it belongs to, then the product.
 */
export function usePageTitle(workspaceName?: string) {
  const { pathname } = useLocation();
  const { t, i18n } = useTranslation();
  useEffect(() => {
    const section = sectionName(pathname);
    const name = pageName(pathname);
    document.title = [
      section && t(section),
      name && t(name),
      workspaceName,
      PRODUCT,
    ]
      .filter(Boolean)
      .join(" · ");
  }, [pathname, workspaceName, t, i18n.resolvedLanguage]);
}
