import type { DisplayItem } from "./display";

/**
 * Test builders for a committed display, written in stream order: each Item
 * opens at the next position of its attempt, and a call ends where `finish`
 * names it.
 */
export const TIME = "2026-09-12T00:00:00Z";

export function item(
  id: string,
  kind: DisplayItem["kind"],
  content: Record<string, unknown>,
  { attempt = 1, occurredAt = TIME } = {},
): DisplayItem {
  return {
    id,
    kind,
    state: kind === "tool_call" ? "in_progress" : "completed",
    first_stream_id: `${attempt}-0`,
    last_stream_id: `${attempt}-0`,
    started_at: occurredAt,
    ended_at: kind === "observation" ? occurredAt : null,
    content: content as DisplayItem["content"],
  };
}
let observations = 0;
export function observation(
  name: string,
  value: unknown,
  options: { attempt?: number; occurredAt?: string } = {},
) {
  return item(`obs_${++observations}`, "observation", { name, value }, options);
}
export function lifecycle(
  type: string,
  request = "model-request-1",
  extra: Record<string, unknown> = {},
  options: { attempt?: number; occurredAt?: string } = {},
) {
  return observation(
    "a13n.harness.lifecycle",
    { event: { payload: { type, request_id: request, ...extra } } },
    options,
  );
}
export function custom(
  name: string,
  payload: Record<string, unknown>,
  attempt = 1,
) {
  return observation(name, { event: { payload } }, { attempt });
}
/** A native Capability event carries its fields on the envelope, not a payload. */
export function capability(name: string, fields: Record<string, unknown>) {
  return observation(name, { event: { kind: name, ...fields } });
}
export function tool(
  name: string,
  id: string,
  fields: Record<string, unknown> = {},
  occurredAt = TIME,
) {
  return item(
    id,
    "tool_call",
    { toolCallName: name, toolCallId: id, ...fields },
    { occurredAt },
  );
}
export function message(
  id: string,
  role: string,
  fields: Record<string, unknown> = {},
) {
  return item(id, "text_message", { role, ...fields });
}
export function reasoning(id: string, text: string) {
  return item(id, "reasoning_message", { text });
}

/** A call's last event: its result, or the observation `on` that failed it. */
export interface Finish {
  call: DisplayItem;
  state: "completed" | "failed";
  occurredAt: string;
  on?: DisplayItem;
  content: Record<string, unknown>;
}
export function finish(
  call: DisplayItem,
  {
    state = "completed",
    occurredAt = TIME,
    on,
    ...content
  }: {
    state?: "completed" | "failed";
    occurredAt?: string;
    on?: DisplayItem;
  } & Record<string, unknown> = {},
): Finish {
  return { call, state, occurredAt, on, content };
}

/** The display's Items, each at its position, with every call ended where `finish` names it. */
export function display(...entries: (DisplayItem | Finish)[]): DisplayItem[] {
  let sequence = 0;
  const items: DisplayItem[] = [];
  for (const entry of entries) {
    if ("call" in entry) {
      const { call } = entry;
      const attempt = call.first_stream_id.split("-")[0];
      call.state = entry.state;
      call.last_stream_id =
        entry.on?.first_stream_id ?? `${attempt}-${++sequence}`;
      call.ended_at = entry.occurredAt;
      call.content = { ...call.content, ...entry.content };
    } else {
      const attempt = entry.first_stream_id.split("-")[0];
      entry.first_stream_id = entry.last_stream_id = `${attempt}-${++sequence}`;
      items.push(entry);
    }
  }
  return items;
}
