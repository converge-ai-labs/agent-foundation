import type { ComponentType } from "react";
import type { Schema } from "../shared/api";

/** A label can follow Console's language without adding keys to its catalog. */
export type AppText = string | { en: string; "zh-CN"?: string };
export interface AppPage {
  /** A URL segment local to this App. */
  id: string;
  label: AppText;
  description?: AppText;
  component: ComponentType;
  permission?: Schema["Verb"];
}
export interface AppSettings extends AppPage {
  scope: "workspace" | "organization";
}
/** The default export of a prebuilt module importing @a13n/console. */
export interface AppContribution {
  bridge: 1;
  pages?: readonly AppPage[];
  settings?: readonly AppSettings[];
  /** Owns only the workspace index; Agents and core routes remain addressable. */
  workspaceHome?: ComponentType;
}
export type InstalledApp = Schema["AppView"];
export interface LoadedApp {
  app: InstalledApp;
  contribution?: AppContribution;
  error?: Error;
}
export function appText(text: AppText, language: string): string {
  return typeof text === "string"
    ? text
    : language.toLowerCase().startsWith("zh")
      ? (text["zh-CN"] ?? text.en)
      : text.en;
}
export const settingsKey = (appId: string, sectionId: string) =>
  `app.${appId}.${sectionId}`;

function isAppText(value: unknown): value is AppText {
  return (
    typeof value === "string" ||
    (value !== null &&
      typeof value === "object" &&
      "en" in value &&
      typeof value.en === "string" &&
      (!("zh-CN" in value) || typeof value["zh-CN"] === "string"))
  );
}

/** Namespace ownership comes from Service, never from module-supplied URLs. */
export function validateContribution(value: AppContribution): AppContribution {
  if (!value || value.bridge !== 1)
    throw new Error("This App requires an unsupported Console bridge.");
  for (const entries of [value.pages ?? [], value.settings ?? []]) {
    const ids = new Set<string>();
    for (const entry of entries) {
      const key = "scope" in entry ? `${entry.scope}:${entry.id}` : entry.id;
      if (!/^[a-z][a-z0-9-]*$/.test(entry.id) || ids.has(key))
        throw new Error(`Invalid or duplicate App contribution: ${key}`);
      ids.add(key);
      if (
        !entry.component ||
        !isAppText(entry.label) ||
        (entry.description !== undefined && !isAppText(entry.description))
      )
        throw new Error(`Incomplete or invalid App contribution text: ${key}`);
      if (
        "scope" in entry &&
        !["workspace", "organization"].includes(String(entry.scope))
      )
        throw new Error(`Unsupported App settings scope: ${entry.scope}`);
    }
  }
  return value;
}
