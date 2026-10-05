/** Compact display snapshots and raw-event normalization. Hosts own coverage, persistence and rendering. */
export interface DisplayItem {
  id: string;
  ordinal: number;
  kind: "text_message" | "reasoning_message" | "tool_call" | "observation";
  state: "in_progress" | "completed" | "interrupted" | "failed";
  first_stream_id: string;
  last_stream_id: string;
  started_at: string;
  ended_at?: string | null;
  content: Record<string, unknown>;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const states = new Set(["in_progress", "completed", "interrupted", "failed"]);
const kinds = new Set([
  "text_message",
  "reasoning_message",
  "tool_call",
  "observation",
]);
const position = (value: unknown) =>
  typeof value === "string" && /^\d+-\d+$/.test(value);
const time = (value: unknown) =>
  typeof value === "string" && Number.isFinite(Date.parse(value));

export function isDisplayItem(item: unknown): item is DisplayItem {
  return (
    record(item) &&
    typeof item.id === "string" &&
    Number.isSafeInteger(item.ordinal) &&
    Number(item.ordinal) > 0 &&
    kinds.has(String(item.kind)) &&
    states.has(String(item.state)) &&
    position(item.first_stream_id) &&
    position(item.last_stream_id) &&
    time(item.started_at) &&
    (item.ended_at == null || time(item.ended_at)) &&
    record(item.content)
  );
}

/** Raw AG-UI remains the live protocol; these are Host identity/position fields. */
export type ItemRef = Pick<DisplayItem, "id" | "kind" | "state"> & {
  ordinal?: number | null;
  response_group?: string | null;
  failure?: Record<string, unknown> | null;
};
export interface DisplayEvent {
  run_id: string;
  attempt: number;
  sequence: number;
  event: Record<string, unknown>;
  item: ItemRef | null;
}
export function isItemRef(value: unknown): value is ItemRef {
  return (
    record(value) &&
    typeof value.id === "string" &&
    kinds.has(String(value.kind)) &&
    states.has(String(value.state)) &&
    (value.ordinal == null ||
      (Number.isSafeInteger(value.ordinal) && Number(value.ordinal) > 0)) &&
    (value.response_group == null ||
      typeof value.response_group === "string") &&
    (value.failure == null || record(value.failure))
  );
}

interface Assembly {
  count: number;
  parts: string[];
  size: number;
}
export interface DisplayContinuation {
  next_ordinal?: number;
  full_content?: boolean;
  response_groups?: Record<string, string>;
  fragments?: {
    pending?: Record<
      string,
      { count: number; parts?: string[]; size?: number }
    >;
    gap?: boolean;
    max_bytes?: number;
    max_pending?: number;
  };
}
const object = (value: unknown): Record<string, unknown> =>
  record(value) ? value : {};
const copied = [
  "messageId",
  "role",
  "toolCallId",
  "toolCallName",
  "parentMessageId",
  "metadata",
  "subagentRunId",
  "responseGroup",
];
const accumulation: Record<string, string> = {
  TEXT_MESSAGE_CONTENT: "text",
  REASONING_MESSAGE_CONTENT: "text",
  TOOL_CALL_ARGS: "arguments",
};
const observationLimit = 32768;
const fieldLimit = 262144;
// Python's display limits count Unicode code points, not UTF-16 code units.
const characters = (text: string) => Array.from(text);
const jsonSize = (value: unknown) =>
  characters(JSON.stringify(value) ?? "null").reduce(
    (size, char) =>
      size +
      (char.codePointAt(0)! > 0xffff ? 12 : char.charCodeAt(0) > 127 ? 6 : 1),
    0,
  );

/** Stateful normalization of a snapshot's intact raw-event suffix. No delivery journal. */
export class DisplayNormalizer {
  private next: number;
  private full: boolean;
  private groups: Record<string, string>;
  private fragments: {
    pending: Record<string, Assembly>;
    gap: boolean;
    max_bytes: number;
    max_pending: number;
  };
  constructor(state?: DisplayContinuation | null) {
    this.next = state?.next_ordinal ?? 1;
    this.full = state?.full_content ?? false;
    this.groups = structuredClone(state?.response_groups ?? {});
    const fragments = state?.fragments;
    this.fragments = {
      pending: Object.fromEntries(
        Object.entries(fragments?.pending ?? {}).map(([id, part]) => [
          id,
          {
            count: part.count,
            parts: [...(part.parts ?? [])],
            size: part.size ?? 0,
          },
        ]),
      ),
      gap: fragments?.gap ?? false,
      max_bytes: fragments?.max_bytes ?? 64 * 1024 * 1024,
      max_pending: fragments?.max_pending ?? 8,
    };
  }
  get incomplete() {
    return this.fragments.gap;
  }
  /** Missing bytes invalidate open JSON assemblies, never parse their suffix as a whole value. */
  gap() {
    this.fragments.pending = {};
    this.fragments.gap = true;
  }
  private bounded(
    content: Record<string, unknown>,
    key: string,
    value: string,
  ) {
    const chars = characters(value);
    content[key] = this.full ? value : chars.slice(0, fieldLimit).join("");
    if (!this.full && chars.length > fieldLimit) content.truncated = true;
  }
  private assemble(
    event: Record<string, unknown>,
  ): Record<string, unknown> | undefined {
    if (event.name !== "a13n.stream.fragment") return event;
    const value = object(event.value);
    const { id, index, count, data } = value;
    if (
      typeof id !== "string" ||
      !Number.isInteger(index) ||
      !Number.isInteger(count) ||
      typeof data !== "string" ||
      Number(index) < 0 ||
      Number(index) >= Number(count)
    ) {
      this.gap();
      return;
    }
    const pending = this.fragments.pending;
    if (index === 0) {
      delete pending[id];
      while (Object.keys(pending).length >= this.fragments.max_pending) {
        delete pending[Object.keys(pending)[0]!];
        this.fragments.gap = true;
      }
      pending[id] = { count: Number(count), parts: [], size: 0 };
    }
    const assembly = pending[id];
    const size = new TextEncoder().encode(data).byteLength;
    if (
      !assembly ||
      assembly.count !== count ||
      assembly.parts.length !== index ||
      Object.values(pending).reduce((n, part) => n + part.size, 0) + size >
        this.fragments.max_bytes
    ) {
      delete pending[id];
      this.fragments.gap = true;
      return;
    }
    assembly.parts.push(data);
    assembly.size += size;
    if (assembly.parts.length !== count) return;
    delete pending[id];
    try {
      const result: unknown = JSON.parse(assembly.parts.join(""));
      if (
        record(result) &&
        result.type === "CUSTOM" &&
        typeof result.name === "string" &&
        result.name !== "a13n.stream.fragment"
      )
        return result;
    } catch {
      /* A damaged assembly is not a domain event. */
    }
    this.fragments.gap = true;
  }
  apply(items: Map<string, DisplayItem>, delta: DisplayEvent) {
    const event =
      delta.event.type === "CUSTOM" ? this.assemble(delta.event) : delta.event;
    if (!event) return;
    const type = String(event.type),
      name = String(event.name);
    const value = object(event.value),
      source = object(value.event),
      payload = object(source.payload);
    const scope = String(event.subagentRunId || "root");
    if (
      name === "a13n.harness.lifecycle" &&
      payload.type === "model_request_started"
    )
      this.groups[scope] = `${delta.run_id}:${delta.attempt}:${delta.sequence}`;
    const ref = delta.item;
    if (!ref) return;
    const previous = items.get(ref.id);
    let content: Record<string, unknown> = { ...previous?.content };
    for (const key of copied) if (key in event) content[key] = event[key];
    if (
      this.groups[scope] &&
      !("responseGroup" in content) &&
      ref.kind !== "observation"
    )
      content.responseGroup = this.groups[scope];
    const field = accumulation[type];
    if (field)
      this.bounded(
        content,
        field,
        String(content[field] ?? "") + String(event.delta ?? ""),
      );
    if (type === "TOOL_CALL_END") content.arguments_complete = true;
    if (type === "REASONING_ENCRYPTED_VALUE")
      content.encrypted_value = event.encryptedValue;
    if (type === "TOOL_CALL_RESULT") {
      if (Array.isArray(event.content)) {
        content.result_parts =
          this.full || jsonSize(event.content) <= fieldLimit
            ? event.content
            : [];
        if (!(content.result_parts as unknown[]).length)
          content.truncated = true;
      } else this.bounded(content, "result", String(event.content ?? ""));
    }
    if (type === "CUSTOM") {
      if (
        name === "a13n.input.user" ||
        name === "a13n.input.steering" ||
        (name === "a13n.input.media" &&
          ["user", "steering"].includes(String(source.source)))
      ) {
        content = {
          messageId: source.message_id,
          role: "user",
          input_source: source.source ?? "user",
          input_group: source.input_id ?? null,
        };
        for (const key of ["metadata", "subagentRunId"])
          if (key in event) content[key] = event[key];
        if (name === "a13n.input.media") content.input_media = source.content;
        else this.bounded(content, "text", String(source.content));
      } else if (ref.kind === "tool_call") {
        const part = object(source.part);
        const native = ["builtin-tool-call", "builtin-tool-return"].includes(
          String(part.part_kind),
        );
        const supplement = [
          "a13n.filesystem.edit_applied",
          "a13n.harness-ui.tool_images",
          "a13n.harness-ui.mcp_apps",
        ].includes(name);
        const tool = supplement ? source : part;
        content.toolCallId = tool.tool_call_id;
        if ("tool_name" in tool) content.toolCallName = tool.tool_name;
        if (native) content.provider = tool.provider_name || "provider";
        if (supplement) {
          if (name === "a13n.filesystem.edit_applied")
            content.applied_edit = {
              file_path: tool.file_path,
              before: tool.before,
              after: tool.after,
            };
          else if (name === "a13n.harness-ui.tool_images") {
            content.tool_images = tool.images ?? [];
            content.tool_image_unavailable = tool.unavailable ?? false;
          } else content.mcp_apps = tool.apps ?? [];
        } else if (tool.part_kind === "builtin-tool-call") {
          this.bounded(
            content,
            "arguments",
            typeof tool.args === "string"
              ? tool.args
              : JSON.stringify(tool.args),
          );
          content.arguments_complete = true;
        } else {
          if (this.full || jsonSize(tool.content) <= fieldLimit)
            content.value = tool.content;
          else content.truncated = true;
          content.outcome = tool.outcome ?? null;
          content.retry = tool.part_kind === "retry-prompt";
        }
      } else {
        let observed: unknown = event.value;
        // The producer's item ref coalesces consecutive argument observations.
        if (name === "a13n.pydantic_ai.part_delta" && previous) {
          if (object(previous.content.value).omitted === true)
            observed = { omitted: true };
          else {
            const oldSource = object(object(previous.content.value).event);
            const oldDelta = object(oldSource.delta),
              partDelta = object(source.delta);
            if (
              typeof oldDelta.args_delta === "string" &&
              typeof partDelta.args_delta === "string"
            )
              observed = {
                ...object(previous.content.value),
                event: {
                  ...oldSource,
                  delta: {
                    ...oldDelta,
                    args_delta: oldDelta.args_delta + partDelta.args_delta,
                  },
                },
              };
          }
        }
        const summary = [
          "a13n.context.handoff_summary",
          "a13n.context.compaction_summary",
        ].includes(name);
        if (!(this.full && summary) && jsonSize(observed) > observationLimit)
          observed = { omitted: true };
        content = { name, value: observed };
        for (const key of ["metadata", "subagentRunId"])
          if (key in event) content[key] = event[key];
      }
    } else if (ref.kind === "observation") content = { ...event };
    if (ref.response_group) content.responseGroup = ref.response_group;
    if (ref.failure) content.failure = structuredClone(ref.failure);
    // A suffix without START is useful, but END cannot turn missing text/JSON into a complete value.
    if (
      previous?.content.incomplete ||
      (!previous &&
        (field || type.endsWith("_END") || type === "TOOL_CALL_RESULT"))
    )
      content.incomplete = true;
    if (content.incomplete && ref.kind === "tool_call")
      content.arguments_complete = false;
    const position = `${delta.attempt}-${delta.sequence}`;
    const timestamp =
      ref.kind === "observation" ? delta.event.timestamp : event.timestamp;
    const at =
      name === "a13n.pydantic_ai.part_delta" && previous
        ? previous.started_at
        : typeof timestamp === "number"
          ? new Date(timestamp).toISOString()
          : new Date().toISOString();
    const ordinal = previous?.ordinal ?? ref.ordinal ?? this.next;
    this.next = Math.max(this.next, ordinal + 1);
    items.set(ref.id, {
      id: ref.id,
      kind: ref.kind,
      state: ref.state,
      ordinal,
      first_stream_id: previous?.first_stream_id ?? position,
      last_stream_id: position,
      started_at: previous?.started_at ?? at,
      ended_at: ["completed", "failed"].includes(ref.state) ? at : null,
      content,
    });
  }
}
