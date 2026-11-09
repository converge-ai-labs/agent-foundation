import type { Schema } from "../../shared/api";

export type FileGuidance = Required<Schema["FileMemoryGuidance"]>;

export function defaultGuidance(): FileGuidance {
  return {
    system_preset: "general",
    modules: null,
    system_override: null,
    custom: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
}

export function selectSystemPreset(
  value: FileGuidance,
  preset: FileGuidance["system_preset"],
): FileGuidance {
  return {
    ...value,
    system_preset: preset,
    modules: null,
    system_override: null,
  };
}

export function validGuidance(value: FileGuidance): boolean {
  if (!value.timezone.trim()) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: value.timezone });
    return true;
  } catch {
    return false;
  }
}
