import type { PresentedItem } from "../projection";
import { parseItemValue } from "../projection";

/**
 * One transcript reading: the agent's prose is its own block and every other
 * item (tools, reasoning, unknown kinds) joins the surrounding execution group.
 */
export type TranscriptBlock =
  | { kind: "message"; id: string; item: PresentedItem }
  | { kind: "execution"; id: string; items: PresentedItem[] };

const presentable = (item: PresentedItem) =>
  item.display !== false &&
  item.kind !== "run_output" &&
  (item.kind !== "text_message" || item.role === "assistant");

export function transcriptBlocks(
  items: readonly PresentedItem[],
): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  for (const item of items.filter(presentable)) {
    if (item.kind === "text_message") {
      blocks.push({ kind: "message", id: item.id, item });
      continue;
    }
    const last = blocks.at(-1);
    if (last?.kind === "execution") last.items.push(item);
    else blocks.push({ kind: "execution", id: item.id, items: [item] });
  }
  return blocks;
}

/**
 * An item state alone cannot say whether an unfinished step is still working:
 * that answer belongs to the run that owns it.
 */
export type ExecutionState =
  | { affordance: "done" }
  | { affordance: "working" }
  | { affordance: "state"; state: string };

export function executionState(
  item: PresentedItem,
  runState = "running",
): ExecutionState {
  if (item.state === "completed") return { affordance: "done" };
  if (item.state !== "in_progress")
    return { affordance: "state", state: item.state };
  if (["running", "queued"].includes(runState))
    return { affordance: "working" };
  if (runState === "waiting") return { affordance: "state", state: "waiting" };
  return {
    affordance: "state",
    state: ["failed", "cancelled"].includes(runState)
      ? "interrupted"
      : "unknown",
  };
}

/** The row explains itself before it is opened: one line of its own content. */
export function executionSummary(item: PresentedItem): string {
  if (item.kind === "reasoning_message") return excerpt(item.text);
  const parsed = parseItemValue(item.arguments);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const value = Object.values(parsed as Record<string, unknown>).find(
      (entry) => entry !== undefined && entry !== null && entry !== "",
    );
    if (value !== undefined) return excerpt(text(value));
  }
  if (typeof parsed === "string" && parsed.trim()) return excerpt(parsed);
  if (item.result !== undefined)
    return excerpt(text(parseItemValue(item.result)));
  if (item.detail !== undefined) return excerpt(text(item.detail));
  return "";
}

/** Item kinds are wire names; a reader sees words. */
export function humanKind(kind: string): string {
  return kind
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());
}

function text(value: unknown): string {
  return typeof value === "string" ? value : (JSON.stringify(value) ?? "");
}

function excerpt(value: string): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > 140 ? `${flat.slice(0, 139)}…` : flat;
}
