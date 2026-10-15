import type { Schema } from "../../shared/api";
import type { GuideDraft } from "./form";

export const defaultSessionMemory: Schema["SessionMemorySettings"] = {
  enabled: false,
  spaces: [],
};
export function validSpaceSelection(
  value: Schema["SessionMemorySettings"],
): boolean {
  const spaces = value.spaces ?? [];
  return (
    spaces.every((item) => /^[a-z][a-z0-9-]{0,62}$/.test(item.name)) &&
    new Set(spaces.map((item) => item.name)).size === spaces.length
  );
}
export function spaceDraft(space: Schema["MemorySpace"]) {
  return {
    name: space.name,
    description: space.description ?? "",
    enabled: space.enabled,
    guide: {
      mode:
        space.guide === null
          ? "inherit"
          : space.guide === ""
            ? "none"
            : "custom",
      text: space.guide ?? "",
    } as GuideDraft,
    alwaysLoad: space.always_load,
    automatic: !!space.organization.enabled,
    model: space.organization.model ?? "",
    checkInterval: String(space.organization.check_interval_seconds ?? 10),
    cooldown: String(space.organization.cooldown_seconds ?? 300),
    trigger: space.organization.trigger?.type ?? "any_change",
    changeCount: String(
      space.organization.trigger?.type === "change_count"
        ? space.organization.trigger.count
        : 10,
    ),
  };
}
export type SpaceDraft = ReturnType<typeof spaceDraft>;
export function spaceUpdate(
  base: Schema["MemorySpace"],
  draft: SpaceDraft,
): Schema["MemorySpaceUpdate"] {
  const guide =
    draft.guide.mode === "inherit"
      ? null
      : draft.guide.mode === "none"
        ? ""
        : draft.guide.text;
  const next: Schema["MemorySpaceUpdate"] = {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    enabled: draft.enabled,
    guide,
    always_load: draft.alwaysLoad,
    organization: {
      enabled: draft.automatic,
      model: draft.model || null,
      check_interval_seconds: Number(draft.checkInterval),
      cooldown_seconds: Number(draft.cooldown),
      trigger:
        draft.trigger === "change_count"
          ? { type: "change_count", count: Number(draft.changeCount) }
          : { type: "any_change" },
    },
  };
  return Object.fromEntries(
    Object.entries(next).filter(
      ([key, value]) =>
        JSON.stringify(value) !==
        JSON.stringify(base[key as keyof typeof base]),
    ),
  );
}
function validInteger(value: string, min: number, max: number) {
  const number = Number(value);
  return (
    value.trim() !== "" &&
    Number.isInteger(number) &&
    number >= min &&
    number <= max
  );
}
export function validSpaceDraft(draft: SpaceDraft) {
  return (
    !!draft.name.trim() &&
    (!draft.automatic || !!draft.model) &&
    validInteger(draft.checkInterval, 10, 86400) &&
    validInteger(draft.cooldown, 0, 604800) &&
    (draft.trigger !== "change_count" ||
      validInteger(draft.changeCount, 1, 1000000)) &&
    (draft.guide.mode !== "custom" || !!draft.guide.text.trim())
  );
}
