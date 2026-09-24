import type { Schema } from "../../shared/api";

type Memory = Schema["Memory"];

/**
 * A memory's guide has three states: `null` inherits the deployment's guide,
 * `""` gives the memory none, and any other text is its own.
 */
export type GuideMode = "inherit" | "custom" | "none";
export interface GuideDraft {
  mode: GuideMode;
  text: string;
}

export function guideDraft(guide: string | null): GuideDraft {
  if (guide === null) return { mode: "inherit", text: "" };
  return guide ? { mode: "custom", text: guide } : { mode: "none", text: "" };
}

export function guideValue(draft: GuideDraft): string | null {
  if (draft.mode === "inherit") return null;
  return draft.mode === "custom" ? draft.text : "";
}

/** What the create form and the Configuration tab edit. */
export interface MemoryDraft {
  name: string;
  description: string;
  guide: GuideDraft;
  alwaysLoad: string[];
}

export function memoryDraft(memory?: Memory): MemoryDraft {
  return {
    name: memory?.name ?? "",
    description: memory?.description ?? "",
    guide: guideDraft(memory ? memory.guide : null),
    alwaysLoad: memory?.always_load ?? [],
  };
}

/** Drafts that would save the same memory, whatever text a hidden guide mode keeps. */
export function sameDraft(a: MemoryDraft, b: MemoryDraft): boolean {
  const saved = (draft: MemoryDraft) => ({
    ...draft,
    guide: guideValue(draft.guide),
  });
  return JSON.stringify(saved(a)) === JSON.stringify(saved(b));
}

/**
 * The draft's own changes from `previous`, carried onto `current`: a field
 * the draft left alone takes the newer saved value instead of undoing it.
 */
export function rebaseDraft(
  previous: MemoryDraft,
  draft: MemoryDraft,
  current: MemoryDraft,
): MemoryDraft {
  const changed = <K extends keyof MemoryDraft>(key: K) =>
    JSON.stringify(draft[key]) !== JSON.stringify(previous[key]);
  return {
    name: changed("name") ? draft.name : current.name,
    description: changed("description")
      ? draft.description
      : current.description,
    guide: changed("guide") ? draft.guide : current.guide,
    alwaysLoad: changed("alwaysLoad") ? draft.alwaysLoad : current.alwaysLoad,
  };
}

function fields(draft: MemoryDraft) {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    guide: guideValue(draft.guide),
    always_load: draft.alwaysLoad,
  };
}

/**
 * Where a new record memory keeps its records: a Memory Provider, and the
 * provider's existing namespace to adopt, or none for a new one.
 */
export interface RecordBackend {
  provider: Schema["Provider"];
  namespace: string;
}

/** A file memory the Service stores, or a record memory on `record`'s provider. */
export function memoryCreate(
  key: string,
  draft: MemoryDraft,
  record?: RecordBackend,
): Schema["MemoryCreate"] {
  if (!record) return { key, type: "postgres", ...fields(draft) };
  const { always_load: _, ...rest } = fields(draft);
  const namespace = record.namespace.trim();
  return {
    key,
    type: record.provider.type,
    provider_id: record.provider.id,
    ...(namespace && { namespace }),
    ...rest,
  };
}

/** Only the fields that differ from the saved memory, so a save changes nothing else. */
export function memoryUpdate(
  saved: Memory,
  draft: MemoryDraft,
): Schema["MemoryUpdate"] {
  const next = fields(draft);
  const same = (a: unknown, b: unknown) =>
    JSON.stringify(a) === JSON.stringify(b);
  return {
    ...(next.name !== saved.name && { name: next.name }),
    ...(next.description !== saved.description && {
      description: next.description,
    }),
    ...(next.guide !== saved.guide && { guide: next.guide }),
    ...(!same(next.always_load, saved.always_load) && {
      always_load: next.always_load,
    }),
  };
}
