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

export function guidanceModules(value: FileGuidance) {
  return (
    value.modules ??
    (value.system_preset === "journal" ? ["topics", "journal"] : ["topics"])
  );
}

export function guidancePreview(
  value: FileGuidance,
  catalog: Schema["MemoryGuidancePresets"] | undefined,
) {
  if (value.system_override !== null) return value.system_override;
  if (value.modules == null)
    return catalog?.items.find((item) => item.id === value.system_preset)
      ?.system_guide;
  const general = catalog?.items.find(
    (item) => item.id === "general",
  )?.system_guide;
  const topics = catalog?.modules?.find((item) => item.id === "topics")?.guide;
  if (!general || !topics) return undefined;
  // The general preset is common rules followed by the catalog's topic module.
  return (
    general.slice(0, -topics.length) +
    (catalog?.modules ?? [])
      .filter((item) => value.modules!.includes(item.id))
      .map((item) => item.guide)
      .join("")
  );
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
