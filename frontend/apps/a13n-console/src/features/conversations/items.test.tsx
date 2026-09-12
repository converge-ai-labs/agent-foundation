import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PresentedItems } from "./items";
import type { PresentedItem } from "./projection";

function item(
  id: string,
  kind: string,
  fields: Partial<PresentedItem> = {},
): PresentedItem {
  return {
    id,
    kind,
    state: "completed",
    parentId: null,
    firstCursor: "1-0",
    lastCursor: "1-0",
    text: "",
    role: "assistant",
    toolName: "",
    arguments: "",
    protectedReasoning: false,
    ...fields,
  };
}
it("places one agent identity before its reasoning, tools, and answer in observed order", () => {
  const markup = renderToStaticMarkup(
    <PresentedItems
      agentName="Release reviewer"
      runState="completed"
      items={[
        item("input", "text_message", { role: "user", text: "Private input" }),
        item("thought", "reasoning_message"),
        item("tool-1", "tool_call", { toolName: "search_docs" }),
        item("tool-2", "tool_call", { toolName: "read_file" }),
        item("reply", "text_message", { text: "Final response" }),
      ]}
    />,
  );
  expect(markup).not.toContain("Private input");
  const labels = [
    "Release reviewer",
    "Reasoning summary",
    "search_docs",
    "read_file",
    "Final response",
  ];
  const positions = labels.map((label) => markup.indexOf(label));
  expect(positions.every((position) => position >= 0)).toBe(true);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  expect(markup.match(/<strong>Release reviewer<\/strong>/g)).toHaveLength(1);
});
it("does not keep unresolved tools spinning after cancellation or relabel a completed tool as waiting", () => {
  const items = [
    item("done", "tool_call", { toolName: "finished_tool" }),
    item("open", "tool_call", { state: "streaming", toolName: "pending_tool" }),
  ];
  const cancelled = renderToStaticMarkup(
    <PresentedItems items={items} runState="cancelled" />,
  );
  expect(cancelled).toContain('data-state="interrupted"');
  expect(cancelled).not.toContain('aria-label="Working"');
  const waiting = renderToStaticMarkup(
    <PresentedItems items={items} runState="waiting" />,
  );
  expect(waiting).toContain('aria-label="Completed"');
  expect(waiting).toContain('data-state="waiting"');
});
it("keeps fallback output and error details inside the same agent response", () => {
  const markup = renderToStaticMarkup(
    <PresentedItems items={[]} agentName="Reviewer">
      <p>Fallback output</p>
      <p>Failure detail</p>
    </PresentedItems>,
  );
  expect(markup).toMatch(
    /Agent response.*Reviewer.*Fallback output.*Failure detail.*<\/section>/,
  );
});
