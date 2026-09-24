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

/** Labels as `key:value` pairs separated by commas or lines, the way label filters name them. */
export function parseLabels(text: string): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const entry of text.split(/[,\n]/)) {
    const pair = entry.trim();
    if (!pair) continue;
    const colon = pair.indexOf(":");
    if (colon <= 0) throw new Error("Write each label as key:value.");
    labels[pair.slice(0, colon).trim()] = pair.slice(colon + 1).trim();
  }
  return labels;
}

/** Why the labels cannot be read, if they cannot. */
export function labelsError(text: string): string | undefined {
  try {
    parseLabels(text);
    return undefined;
  } catch (error) {
    return (error as Error).message;
  }
}

export function formatLabels(labels: Record<string, string>): string {
  return Object.entries(labels)
    .map(([key, value]) => `${key}:${value}`)
    .join(", ");
}

/** What the create form and the Configuration tab edit. */
export interface MemoryDraft {
  name: string;
  description: string;
  labels: string;
  guide: GuideDraft;
  alwaysLoad: string[];
}

export function memoryDraft(memory?: Memory): MemoryDraft {
  return {
    name: memory?.name ?? "",
    description: memory?.description ?? "",
    labels: formatLabels(memory?.labels ?? {}),
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
    labels: changed("labels") ? draft.labels : current.labels,
    guide: changed("guide") ? draft.guide : current.guide,
    alwaysLoad: changed("alwaysLoad") ? draft.alwaysLoad : current.alwaysLoad,
  };
}

function fields(draft: MemoryDraft) {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    labels: parseLabels(draft.labels),
    guide: guideValue(draft.guide),
    always_load: draft.alwaysLoad,
  };
}

export function memoryCreate(
  key: string,
  draft: MemoryDraft,
): Schema["MemoryCreate"] {
  return { key, type: "postgres", ...fields(draft) };
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
    ...(!same(sortedEntries(next.labels), sortedEntries(saved.labels)) && {
      labels: next.labels,
    }),
    ...(next.guide !== saved.guide && { guide: next.guide }),
    ...(!same(next.always_load, saved.always_load) && {
      always_load: next.always_load,
    }),
  };
}

function sortedEntries(labels: Record<string, string>) {
  return Object.entries(labels).sort(([a], [b]) => a.localeCompare(b));
}
