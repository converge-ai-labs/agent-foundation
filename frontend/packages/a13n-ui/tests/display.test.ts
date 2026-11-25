import { expect, it } from "vitest";
import {
  DisplayNormalizer,
  type DisplayItem,
  type ItemRef,
} from "../src/display";

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
