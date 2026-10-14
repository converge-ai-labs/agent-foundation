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
    organization: { enabled: draft.automatic, model: draft.model || null },
  };
  return Object.fromEntries(
    Object.entries(next).filter(
      ([key, value]) =>
        JSON.stringify(value) !==
        JSON.stringify(base[key as keyof typeof base]),
    ),
  );
}
export function validSpaceDraft(draft: SpaceDraft) {
  return (
    !!draft.name.trim() &&
    (!draft.automatic || !!draft.model) &&
    (draft.guide.mode !== "custom" || !!draft.guide.text.trim())
  );
}
