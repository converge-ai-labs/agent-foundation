import type { Schema } from "../transport/client";
import type { InputPart } from "./input-content";
import type { OrderedInputPart } from "./inline-attachments";
import { skillSpans, type SkillCatalog } from "./skill-references";

export type LocalInput = {
  id: string;
  action: "send" | "steer";
  parts: InputPart[];
  state: "preparing" | "pending" | "accepted" | "rejected" | "unknown";
};

export function inputSource(part: InputPart): string | undefined {
  return typeof part.metadata?.source_id === "string"
    ? part.metadata.source_id
    : undefined;
}

export function previewInput(
  id: string,
  parts: OrderedInputPart[],
  attachments: Map<string, Schema<"ThreadAttachment">> = new Map(),
  catalog?: SkillCatalog,
): InputPart[] {
  return parts.map((part, index) => ({
    kind: "user",
    text:
      typeof part === "string"
        ? part
        : attachments.has(part.attachment_id)
          ? ""
          : "[Attachment]",
    metadata: {
      source_id: id,
      harness_ui: {
        composer: { index },
        ...(typeof part === "string" && catalog
          ? { skills: skillSpans(part, catalog) }
          : {}),
        ...(typeof part !== "string" && attachments.has(part.attachment_id)
          ? { attachment: attachments.get(part.attachment_id) }
          : {}),
      },
    },
  }));
}

// A private display fallback, never a metadata write or evidence of saved input.
export function conversationTitle(
  thread: Schema<"ThreadSummary"> | undefined,
  localInputs: readonly LocalInput[] = [],
) {
  const first = localInputs.find(
    (input) => input.action === "send" && input.state === "accepted",
  );
  return (
    thread?.title ||
    thread?.excerpt?.first_input ||
    first?.parts
      .map((part) => part.text ?? "")
      .join(" ")
      .trim()
      .slice(0, 160) ||
    "Untitled conversation"
  );
}
