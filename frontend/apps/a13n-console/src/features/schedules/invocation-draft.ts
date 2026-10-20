import { parseJsonDraft } from "./json-draft";

// Drafts may be incomplete or contain fields that only submission validation can check.
export type InvocationDraft = {
  agent_id: string;
  agent_revision_id?: string | null;
  payload: {
    content: Record<string, unknown>[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Check only what the fields editor needs, preserving all unrepresented data. */
export function invocationFields(text: string) {
  const value = parseJsonDraft(text);
  if (!object(value)) throw new Error("Invocation must be a JSON object.");
  if (value.agent_id !== undefined && typeof value.agent_id !== "string")
    throw new Error("Agent ID must be text.");
  if (
    value.agent_revision_id != null &&
    typeof value.agent_revision_id !== "string"
  )
    throw new Error("Agent revision must be text or null.");
  if (value.payload !== undefined && !object(value.payload))
    throw new Error("Payload must be a JSON object.");
  const payload = value.payload ?? {};
  const content = payload.content === undefined ? [] : payload.content;
  if (!Array.isArray(content))
    throw new Error("Input content must be an array.");
  const part: unknown = content[0];
  if (
    content.length > 1 ||
    (part !== undefined && (!object(part) || part.type !== "text"))
  )
    throw new Error(
      "Fields require one text input. Use JSON to edit structured input.",
    );
  const textPart = object(part) ? part : {};
  if (textPart.text !== undefined && typeof textPart.text !== "string")
    throw new Error("Input must be text.");
  const input = textPart.text ?? "";
  const draft: InvocationDraft = {
    ...value,
    agent_id: value.agent_id ?? "",
    agent_revision_id: value.agent_revision_id ?? null,
    payload: {
      ...payload,
      content: [{ ...textPart, type: "text", text: input }],
    },
  };
  return { draft, input };
}
