import type { Schema } from "../../shared/api";
import { policyDraft, policyValue, validPolicy } from "./organization-form";
import { defaultGuidance, validGuidance } from "./guidance";

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
    guidance: { ...defaultGuidance(), ...space.guidance },
    alwaysLoad: space.always_load,
    ...policyDraft(space.organization),
  };
}
export type SpaceDraft = ReturnType<typeof spaceDraft>;
export function spaceUpdate(
  base: Schema["MemorySpace"],
  draft: SpaceDraft,
): Schema["MemorySpaceUpdate"] {
  const next: Schema["MemorySpaceUpdate"] = {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    enabled: draft.enabled,
    guidance: draft.guidance,
    always_load: draft.alwaysLoad,
    organization: policyValue(draft),
  };
  return Object.fromEntries(
    Object.entries(next).filter(
      ([key, value]) =>
        JSON.stringify(value) !==
        JSON.stringify(
          key === "organization"
            ? policyValue(policyDraft(base.organization))
            : base[key as keyof typeof base],
        ),
    ),
  );
}
export function validSpaceDraft(draft: SpaceDraft) {
  return (
    !!draft.name.trim() && validPolicy(draft) && validGuidance(draft.guidance)
  );
}
