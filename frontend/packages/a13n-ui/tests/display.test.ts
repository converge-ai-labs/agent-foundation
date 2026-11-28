import { expect, it } from "vitest";
import {
  DisplayNormalizer,
  type DisplayItem,
  type ItemRef,
} from "../src/display";

it.each([false, true])(
  "normalizes fragmented ACP details on their tool item (full content: %s)",
  (full) => {
    const normalizer = new DisplayNormalizer({ full_content: full });
    const items = new Map<string, DisplayItem>();
    const tool = {
      id: "call",
      kind: "tool_call",
      state: "in_progress",
    } as const;
    let sequence = 0;
    const apply = (
      event: Record<string, unknown>,
      item: ItemRef | null = null,
    ) =>
      normalizer.apply(items, {
        run_id: "run",
        attempt: 1,
        sequence: ++sequence,
        event,
        item,
      });
    apply(
      {
        type: "TOOL_CALL_START",
        toolCallId: "read",
        toolCallName: "read_file",
      },
      tool,
    );
    const summary = {
      title: "Read notes",
      kind: "read",
      status: "in_progress",
    };
    const details = {
      ...summary,
      toolCallId: "read",
      locations: [{ path: "/workspace/notes.md" }],
      content: [
        {
          type: "content",
          content: { type: "text", text: "a".repeat(33_000) },
        },
      ],
    };
    const encoded = JSON.stringify({
      type: "CUSTOM",
      name: "a13n.acp.tool",
      value: details,
    });
    const fragment = (index: number, data: string) => ({
      type: "CUSTOM",
      name: "a13n.stream.fragment",
      value: { id: "details", index, count: 2, data },
    });
    apply(fragment(0, encoded.slice(0, 100)));
    expect(items.get(tool.id)?.content.acp).toBeUndefined();
    apply(fragment(1, encoded.slice(100)), tool);
    apply(
      { type: "TOOL_CALL_RESULT", content: "ok" },
      { ...tool, state: "completed" },
    );
    expect(items.size).toBe(1);
    expect(items.get(tool.id)).toMatchObject({
      state: "completed",
      content: { toolCallName: "read_file", result: "ok" },
    });
    expect(items.get(tool.id)?.content.acp).toEqual(full ? details : summary);
    expect(normalizer.incomplete).toBe(false);
  },
);

it("clears progress in the terminal scope and ignores progress on a completed tool", () => {
  const normalizer = new DisplayNormalizer();
  const items = new Map<string, DisplayItem>();
  let sequence = 0;
  const apply = (event: Record<string, unknown>, item: ItemRef | null = null) =>
    normalizer.apply(items, {
      run_id: "run",
      attempt: 1,
      sequence: ++sequence,
      event,
      item,
    });
  const tool = (
    id: string,
    state: DisplayItem["state"] = "in_progress",
  ): ItemRef => ({ id, kind: "tool_call", state });
  const report = (id: string, subagentRunId?: string) =>
    apply(
      {
        type: "CUSTOM",
        name: "a13n.harness.tool_progress",
        subagentRunId,
        value: {
          event: { payload: { tool_call_id: id, message: "Scanning" } },
        },
      },
      tool(id),
    );
  for (const [id, child] of [
    ["root", undefined],
    ["a", "child-a"],
    ["b", "child-b"],
  ] as const) {
    apply(
      {
        type: "TOOL_CALL_START",
        toolCallId: id,
        toolCallName: "scan",
        subagentRunId: child,
      },
      tool(id),
    );
    report(id, child);
    expect(items.get(id)?.content.progress).toMatchObject({
      message: "Scanning",
    });
  }
  apply({ type: "SUBAGENT_FINISHED", subagentRunId: "child-a" });
  expect(items.get("a")?.content.progress).toBeUndefined();
  expect(items.get("b")?.content.progress).toBeDefined();
  expect(items.get("root")?.content.progress).toBeDefined();

  apply(
    { type: "TOOL_CALL_RESULT", toolCallId: "root", content: "done" },
    tool("root", "completed"),
  );
  report("root");
  expect(items.get("root")?.content.progress).toBeUndefined();

  apply({ type: "RUN_ERROR", message: "Stopped" });
  expect(items.get("b")?.content.progress).toBeUndefined();
});

it.each([false, true])(
  "retains large a13n.acp.compaction observations only when full_content is enabled (%s)",
  (full) => {
    const normalizer = new DisplayNormalizer({ full_content: full });
    const items = new Map<string, DisplayItem>();
    const payload = {
      compactionId: "cmp-1",
      status: "completed",
      summary: "x".repeat(40_000),
    };
    normalizer.apply(items, {
      run_id: "run",
      attempt: 1,
      sequence: 1,
      event: { type: "CUSTOM", name: "a13n.acp.compaction", value: payload },
      item: { id: "cmp-item", kind: "observation", state: "completed" },
    });
    expect(items.get("cmp-item")?.content.value).toEqual(
      full ? payload : { omitted: true },
    );
  },
);

it("replays raw scoped citation supplements and an empty replacement without changing item identity", () => {
  const normalizer = new DisplayNormalizer({ full_content: false });
  const items = new Map<string, DisplayItem>();
  let sequence = 0;
  const apply = (event: Record<string, unknown>, item: ItemRef | null = null) =>
    normalizer.apply(items, {
      run_id: "run",
      attempt: 1,
      sequence: ++sequence,
      event,
      item,
    });
  for (const scope of ["root", "child"]) {
    const identity = {
      messageId: "message",
      ...(scope === "child" ? { subagentRunId: scope } : {}),
    };
    const ref = {
      id: scope,
      kind: "text_message",
      state: "in_progress",
      ordinal: sequence + 1,
    } as const;
    apply(
      {
        type: "TEXT_MESSAGE_START",
        role: "assistant",
        timestamp: 10,
        ...identity,
      },
      ref,
    );
    apply(
      {
        type: "TEXT_MESSAGE_CONTENT",
        delta: "Claim",
        timestamp: 20,
        ...identity,
      },
      ref,
    );
    apply(
      { type: "TEXT_MESSAGE_END", timestamp: 30, ...identity },
      { ...ref, state: "completed" },
    );
  }
  const citation = {
    sources: [{ kind: "web", url: "https://example.com" }],
    anchor: { kind: "content", start: 0, end: 5 },
  };
  const supplement = (citations: unknown[], replace?: boolean) => ({
    type: "CUSTOM",
    name: "a13n.text_citations",
    subagentRunId: "child",
    value: { event: { message_id: "message", citations, replace } },
  });
  const child = items.get("child")!;
  apply(supplement([citation, citation]));
  expect(items.get("root")?.content.citations).toBeUndefined();
  expect(items.get("child")).toEqual({
    ...child,
    last_stream_id: "1-7",
    content: { ...child.content, citations: [citation] },
  });
  apply(supplement([], true));
  expect(items.get("child")?.content.citations).toEqual([]);
  expect(normalizer.incomplete).toBe(false);
});
