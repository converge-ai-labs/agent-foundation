// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConversationTranscript, LiveOutput } from "./transcript";
import { PauseConversationFollowing } from "./execution-details";
import { RunActivity } from "./run-activity";
import { FocusDisplay, focusFrame, type DisplayBlock } from "./stream";
import type { Schema } from "../transport/client";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps immediate activity beside the response as tools arrive, outside collapsed details", () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const input: Schema<"TranscriptEntry"> = {
    position: 0,
    message_kind: "request",
    parts: [{ kind: "user", text: "Check the current work" }],
  };
  const tool: DisplayBlock = {
    id: "tool-one",
    kind: "tool",
    name: "shell_exec",
    text: '{"command":"echo checking"}',
    done: true,
  };
  const base = {
    threadId: "one",
    entries: [input],
    localInputs: [],
    turns: [
      {
        turn_id: "one",
        input_position: 0,
        end_position: 1,
        final_position: null,
        preview: "Check the current work",
      },
    ],
  };
  const view = render(
    <ConversationTranscript
      {...base}
      blocks={[]}
      activity={
        <RunActivity submission={{ kind: "pending", action: "send" }} />
      }
    />,
  );
  const message = screen.getByText("Check the current work");
  expect(screen.getByRole("status").textContent).toBe("Sending…");
  expect(
    message.compareDocumentPosition(screen.getByRole("status")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  const working = (
    <RunActivity
      activity={{ state: "running" }}
      submission={{ kind: "idle" }}
    />
  );
  view.rerender(
    <ConversationTranscript {...base} blocks={[tool]} activity={working} />,
  );
  const trigger = screen.getByRole("button", {
    name: /Work log/,
    expanded: false,
  });
  const status = screen.getByRole("status");
  expect(status.textContent).toBe("Working…");
  expect(
    trigger.compareDocumentPosition(status) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(status.closest("[data-execution-reader]")).toBeNull();
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  view.rerender(
    <ConversationTranscript
      {...base}
      blocks={[{ ...tool, result: "checked" }]}
      activity={working}
    />,
  );
  expect(
    screen.getByRole("button", {
      name: /Work log/,
      expanded: false,
    }),
  ).toBe(trigger);
  expect(screen.getByRole("status").textContent).toBe("Working…");
  view.rerender(
    <ConversationTranscript
      {...base}
      blocks={[{ ...tool, result: "checked" }]}
      activity={
        <RunActivity
          activity={{ state: "inactive" }}
          submission={{ kind: "idle" }}
        />
      }
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(
    <ConversationTranscript
      {...base}
      blocks={[
        { ...tool, result: "checked" },
        {
          ...tool,
          id: "tool-two",
          name: "view",
          text: '{"path":"layout.css"}',
          result: "read",
        },
        { ...tool, id: "tool-three", result: "checked again" },
        { id: "thought", kind: "thinking", text: "Next step" },
      ]}
    />,
  );
  expect(trigger.textContent).toContain("Run 2 commands, Read 1 file");
  view.rerender(
    <ConversationTranscript
      {...base}
      running
      blocks={[
        { ...tool, result: "checked" },
        { id: "thought", kind: "thinking", text: "Next step" },
      ]}
    />,
  );
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(
    within(screen.getByRole("region", { name: "Thought content" })).getByText(
      "Next step",
    ),
  ).toBeTruthy();
});

it.each([
  { outcome: "denied" as const },
  { outcome: "interrupted" as const },
  { stopped: true },
  { result: "" },
])(
  "does not label a terminal or empty tool result as running: %j",
  (terminal) => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
    }));
    render(
      <ConversationTranscript
        threadId="one"
        entries={entries.slice(0, 1)}
        localInputs={[]}
        turns={[
          {
            turn_id: "one",
            input_position: 0,
            end_position: 1,
            final_position: null,
            preview: "Check",
          },
        ]}
        blocks={[
          {
            id: "tool-one",
            kind: "tool",
            name: "shell_exec",
            text: "{}",
            done: true,
            ...terminal,
          },
        ]}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /Work log/,
      }),
    ).toBeTruthy();
    expect(screen.queryByText(/running/)).toBeNull();
  },
);

function viewport() {
  let mobile = true;
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return query === "(max-width: 700px)" && mobile;
    },
    addEventListener: (_: string, callback: () => void) =>
      listeners.add(callback),
    removeEventListener: (_: string, callback: () => void) =>
      listeners.delete(callback),
  }));
  return (next: boolean) =>
    act(() => {
      mobile = next;
      listeners.forEach((callback) => callback());
    });
}

const entries: Schema<"TranscriptEntry">[] = [
  {
    position: 0,
    message_kind: "request",
    parts: [{ kind: "user", text: "Investigate scrolling" }],
  },
  {
    position: 1,
    message_kind: "response",
    parts: [
      { kind: "thinking", text: "A long internal plan" },
      { kind: "assistant", text: "Checking the layout" },
    ],
  },
];
function transcript(complete = false) {
  return (
    <ConversationTranscript
      threadId="one"
      entries={
        complete
          ? [
              ...entries,
              {
                position: 2,
                message_kind: "response",
                parts: [{ kind: "assistant", text: "The final answer" }],
              },
            ]
          : entries
      }
      turns={[
        {
          turn_id: "turn-one",
          input_position: 0,
          end_position: complete ? 3 : 2,
          final_position: complete ? 2 : null,
          preview: "Investigate scrolling",
        },
      ]}
      blocks={[]}
      localInputs={[]}
    />
  );
}

it("shows a compact mobile preview and opens only one execution reader on demand", async () => {
  viewport();
  const view = render(transcript());
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.queryByText("A long internal plan")).toBeNull();
  const trigger = screen.getByRole("button", { name: /Work log/ });
  const user = userEvent.setup();
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", {
    name: "Work log",
  });
  expect(within(dialog).getByText("A long internal plan")).toBeTruthy();
  expect(within(dialog).queryByText("Investigate scrolling")).toBeNull();
  expect(document.querySelectorAll("[data-execution-reader]")).toHaveLength(1);
  view.rerender(transcript(true));
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(within(dialog).getByText("Recorded steps")).toBeTruthy();
  expect(within(dialog).queryByText("The final answer")).toBeNull();
  await user.click(
    within(dialog).getByRole("button", { name: "Back to chat" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    screen.getByText("The final answer").closest("[data-execution-reader]"),
  ).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

it("retains Thought disclosure, scroll and reader identity across visits and supports Escape", async () => {
  viewport();
  render(transcript());
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: /Work log/ });
  await user.click(trigger);
  const reader = await screen.findByRole("region", {
    name: "Work log",
  });
  const thought = within(reader).getByText("Thought");
  expect(thought.closest("details")?.open).toBe(false);
  await user.click(thought);
  const reasoning = within(reader).getByRole("region", {
    name: "Thought content",
  });
  fireEvent.scroll(reasoning, { target: { scrollTop: 80 } });
  reader.focus();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(trigger);
  expect(await screen.findByRole("region", { name: "Work log" })).toBe(reader);
  expect(within(reader).getByRole("region", { name: "Thought content" })).toBe(
    reasoning,
  );
  expect(reasoning.scrollTop).toBe(80);
});

it("switches between mobile inspection and the existing desktop disclosure", async () => {
  const resize = viewport();
  render(transcript());
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Work log/ }));
  await screen.findByRole("dialog");
  resize(false);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: /Work log/ })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  resize(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
});

it("mounts desktop details on first expansion and preserves the reader and disclosures on collapse", async () => {
  viewport()(false);
  const view = render(transcript());
  expect(document.querySelectorAll("[data-execution-reader]")).toHaveLength(0);
  expect(screen.queryByText("A long internal plan")).toBeNull();
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: /Work log/ });
  const label = trigger.textContent;
  await user.click(trigger);
  expect(trigger.textContent).toBe(label);
  const reader = screen.getByRole("region", { name: "Work log" });
  await user.click(within(reader).getByText("Thought"));
  const reasoning = within(reader).getByRole("region", {
    name: "Thought content",
  });
  fireEvent.scroll(reasoning, { target: { scrollTop: 80 } });
  await user.click(trigger);
  expect(trigger.textContent).toBe(label);
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(reader.isConnected).toBe(true);
  view.rerender(transcript(true));
  await user.click(trigger);
  expect(screen.getByRole("region", { name: "Work log" })).toBe(reader);
  expect(within(reader).getByRole("region", { name: "Thought content" })).toBe(
    reasoning,
  );
  expect(reasoning.scrollTop).toBe(80);
  expect(
    screen.getByText("The final answer").closest("[data-execution-reader]"),
  ).toBeNull();
});

it("keeps progress and final prose in order outside completed execution details", async () => {
  viewport()(false);
  const saved: Schema<"TranscriptEntry">[] = [
    entries[0],
    {
      position: 1,
      message_kind: "response",
      parts: [
        { kind: "assistant", text: "I will inspect the submit handler." },
        {
          kind: "thinking",
          text: "Consider duplicate requests.",
          duration_ms: 2400,
        },
        {
          kind: "tool_call",
          tool_name: "view",
          tool_call_id: "read",
          value: { path: "checkout.ts" },
        },
        {
          kind: "tool_result",
          tool_name: "view",
          tool_call_id: "read",
          value: "source",
          duration_ms: 1200,
        },
      ],
    },
    {
      position: 2,
      message_kind: "response",
      parts: [{ kind: "assistant", text: "The handler needs a guard." }],
    },
    {
      position: 3,
      message_kind: "response",
      parts: [{ kind: "assistant", text: "The final answer" }],
    },
  ];
  const props = {
    threadId: "one",
    entries: saved,
    blocks: [],
    localInputs: [],
    turns: [
      {
        turn_id: "finished",
        input_position: 0,
        end_position: 4,
        final_position: 3,
        preview: "Investigate scrolling",
        duration_ms: 65200,
      },
    ],
  };
  const view = render(
    <ConversationTranscript
      {...props}
      turns={[{ ...props.turns[0], final_position: null }]}
    />,
  );
  const progress = screen.getByText("I will inspect the submit handler.");
  const finding = screen.getByText("The handler needs a guard.");
  const answer = screen.getByText("The final answer");
  view.rerender(<ConversationTranscript {...props} />);
  const trigger = screen.getByRole("button", {
    name: /^Worked for 1m 5s\s*Work log$/,
    expanded: false,
  });
  expect(screen.getByText("I will inspect the submit handler.")).toBe(progress);
  expect(screen.getByText("The handler needs a guard.")).toBe(finding);
  expect(screen.getByText("The final answer")).toBe(answer);
  const ordered = [progress, trigger, finding, answer];
  for (const [index, node] of ordered.slice(1).entries()) {
    expect(ordered[index].compareDocumentPosition(node)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  }
  const user = userEvent.setup();
  await user.click(trigger);
  const reader = screen.getByRole("region", { name: "Work log" });
  expect(ordered.some((node) => reader.contains(node))).toBe(false);
  expect(within(reader).getByText("checkout.ts")).toBeTruthy();
  expect(within(reader).getByText("for 2s")).toBeTruthy();
  const thought = within(reader).getByText("Thought").closest("details")!;
  expect(thought.open).toBe(false);
  expect(within(reader).queryByText("1s")).toBeNull();
  expect(within(reader).queryByText("The final answer")).toBeNull();
  expect(reader.scrollTop).toBe(0);
  await user.click(trigger);
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.getByText("The final answer")).toBeTruthy();
});

it("does not create an empty Worked disclosure for a direct answer", () => {
  viewport()(false);
  render(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      blocks={[]}
      entries={[
        entries[0],
        {
          position: 1,
          message_kind: "response",
          parts: [{ kind: "assistant", text: "Direct answer" }],
        },
      ]}
      turns={[
        {
          turn_id: "one",
          input_position: 0,
          end_position: 2,
          final_position: 1,
          preview: "Question",
        },
      ]}
    />,
  );
  expect(screen.getByText("Direct answer")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Work log/ })).toBeNull();
});

it("keeps the execution reader open without moving the preceding progress on completion", async () => {
  viewport()(false);
  const history: Schema<"TranscriptEntry">[] = [
    entries[0],
    {
      position: 1,
      message_kind: "response",
      parts: [
        { kind: "assistant", text: "Looking into this now." },
        { kind: "thinking", text: "Plan the checks." },
      ],
    },
  ];
  const props = {
    threadId: "one",
    entries: history,
    blocks: [],
    localInputs: [],
    turns: [
      { turn_id: "one", input_position: 0, end_position: 2, preview: "Check" },
    ],
  };
  const view = render(<ConversationTranscript {...props} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Work log/ }));
  const reader = screen.getByRole("region", { name: "Work log" });
  await user.click(within(reader).getByText("Thought"));
  const thought = within(reader).getByRole("region", {
    name: "Thought content",
  });
  const progress = screen.getByText("Looking into this now.");
  view.rerender(
    <ConversationTranscript
      {...props}
      entries={[
        ...history,
        {
          position: 2,
          message_kind: "response",
          parts: [{ kind: "assistant", text: "Finished." }],
        },
      ]}
      turns={[{ ...props.turns[0], end_position: 3, final_position: 2 }]}
    />,
  );
  expect(screen.getByRole("region", { name: "Work log" })).toBe(reader);
  expect(within(reader).getByRole("region", { name: "Thought content" })).toBe(
    thought,
  );
  expect(screen.getByText("Looking into this now.")).toBe(progress);
  expect(reader.contains(progress)).toBe(false);
  expect(
    screen.getByText("Finished.").closest("[data-execution-reader]"),
  ).toBeNull();
});

it("streams live thinking beneath the single collapsed desktop trigger, collapses completely on tool calls, reappears for new thinking, and settles on completion", () => {
  viewport()(false);
  const pauseFollowing = vi.fn();
  const input: Schema<"TranscriptEntry"> = {
    position: 0,
    message_kind: "request",
    parts: [{ kind: "user", text: "Trace checkout retries" }],
  };
  const turn = {
    turn_id: "live-turn",
    input_position: 0,
    end_position: 1,
    final_position: null,
    preview: "Trace checkout retries",
  };
  const renderTranscript = (
    blocks: DisplayBlock[],
    running = true,
    complete = false,
  ) => (
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={
          complete
            ? [
                input,
                {
                  position: 1,
                  message_kind: "response",
                  parts: [
                    { kind: "thinking", text: "First step\nSecond step" },
                    {
                      kind: "tool_call",
                      tool_name: "view",
                      tool_call_id: "read-1",
                      value: { path: "checkout.ts" },
                    },
                    {
                      kind: "tool_result",
                      tool_name: "view",
                      tool_call_id: "read-1",
                      value: "ok",
                    },
                    { kind: "thinking", text: "Verify retry guard" },
                    { kind: "assistant", text: "Guard added." },
                  ],
                },
              ]
            : [input]
        }
        turns={[
          complete
            ? {
                ...turn,
                end_position: 2,
                final_position: 1,
                duration_ms: 8400,
              }
            : turn,
        ]}
        blocks={blocks}
        localInputs={[]}
        running={running}
      />
    </PauseConversationFollowing>
  );

  // Empty thinking START frame keeps the trigger collapsed and does not flash an empty preview box.
  const view = render(
    renderTranscript([{ id: "t1", kind: "thinking", text: "   " }]),
  );
  const trigger = screen.getByRole("button", { name: /Work log/ });
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(pauseFollowing).not.toHaveBeenCalled();

  // First non-empty delta renders a single Thinking header and live scrolling preview underneath.
  view.rerender(
    renderTranscript([{ id: "t1", kind: "thinking", text: "First step" }]),
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.getAllByText("Thinking")).toHaveLength(1);
  const thoughtViewport = screen.getByRole("region", {
    name: "Thought content",
  });
  let scrollHeight = 320;
  Object.defineProperty(thoughtViewport, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(thoughtViewport, "clientHeight", {
    configurable: true,
    get: () => 144,
  });

  scrollHeight = 460;
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
    ]),
  );
  expect(thoughtViewport.scrollTop).toBe(460);
  expect(pauseFollowing).not.toHaveBeenCalled();

  // Brief gap after REASONING_MESSAGE_END keeps the preview open and settles the single header to Thought.
  view.rerender(
    renderTranscript([
      {
        id: "t1",
        kind: "thinking",
        text: "First step\nSecond step",
        done: true,
      },
    ]),
  );
  expect(trigger.textContent).toBe("ThoughtWork log");
  expect(screen.getByRole("region", { name: "Thought content" })).toBe(
    thoughtViewport,
  );

  // Active tool call removes live thinking preview completely and leaves only the collapsed tool overview.
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
      {
        id: "tool-1",
        kind: "tool",
        name: "view",
        toolCallId: "read-1",
        text: '{"path":"checkout.ts"}',
        done: false,
      },
    ]),
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(screen.queryByText("First step")).toBeNull();
  expect(trigger.textContent).not.toContain("Thinking");
  expect(trigger.textContent).toContain("checkout.ts");

  // Completed tool call still stays collapsed without sticking on Thinking even when earlier thinking omitted done.
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
      {
        id: "tool-1",
        kind: "tool",
        name: "view",
        toolCallId: "read-1",
        text: '{"path":"checkout.ts"}',
        done: true,
        result: "ok",
      },
    ]),
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(trigger.textContent).toContain("Read 1 file");

  // Subsequent thinking block after tool call reappears with a fresh viewport and scrolls to bottom.
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
      {
        id: "tool-1",
        kind: "tool",
        name: "view",
        toolCallId: "read-1",
        text: '{"path":"checkout.ts"}',
        done: true,
        result: "ok",
      },
      { id: "t2", kind: "thinking", text: "Verify retry guard" },
    ]),
  );
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getAllByText("Thinking")).toHaveLength(1);
  const secondViewport = screen.getByRole("region", {
    name: "Thought content",
  });
  expect(secondViewport).not.toBe(thoughtViewport);
  expect(within(secondViewport).getByText("Verify retry guard")).toBeTruthy();

  // Brief gap after second thinking ends keeps Thought header instead of flashing prior tool summary.
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
      {
        id: "tool-1",
        kind: "tool",
        name: "view",
        toolCallId: "read-1",
        text: '{"path":"checkout.ts"}',
        done: true,
        result: "ok",
      },
      { id: "t2", kind: "thinking", text: "Verify retry guard", done: true },
    ]),
  );
  expect(trigger.textContent).toBe("ThoughtWork log");
  expect(screen.getByRole("region", { name: "Thought content" })).toBe(
    secondViewport,
  );

  // Assistant prose follows: live preview disappears and execution stays collapsed without pausing follow.
  view.rerender(
    renderTranscript([
      { id: "t1", kind: "thinking", text: "First step\nSecond step" },
      {
        id: "tool-1",
        kind: "tool",
        name: "view",
        toolCallId: "read-1",
        text: '{"path":"checkout.ts"}',
        done: true,
        result: "ok",
      },
      { id: "t2", kind: "thinking", text: "Verify retry guard" },
      { id: "reply-1", kind: "assistant", text: "Guard added." },
    ]),
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(pauseFollowing).not.toHaveBeenCalled();

  // Turn completes: collapses behind Worked for ... and stays collapsed.
  view.rerender(renderTranscript([], false, true));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(trigger.textContent).toContain("Worked for 8s");
});

it("hides live thinking preview on manual expand without duplication, pauses tail-scroll on upward scroll, and preserves Reasoning overrides", async () => {
  viewport()(false);
  const pauseFollowing = vi.fn();
  const user = userEvent.setup();
  const input: Schema<"TranscriptEntry"> = {
    position: 0,
    message_kind: "request",
    parts: [{ kind: "user", text: "Inspect overrides" }],
  };
  const turn = {
    turn_id: "override-turn",
    input_position: 0,
    end_position: 1,
    final_position: null,
    preview: "Inspect overrides",
  };
  const view = render(
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={[input]}
        turns={[turn]}
        blocks={[{ id: "t1", kind: "thinking", text: "Step one" }]}
        localInputs={[]}
        running
      />
    </PauseConversationFollowing>,
  );

  const trigger = screen.getByRole("button", { name: /Work log/ });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();

  // Scroll up pauses auto-scroll in standalone preview; scrolling back within 4px resumes auto-scroll.
  const previewViewport = screen.getByRole("region", {
    name: "Thought content",
  });
  let scrollHeight = 400;
  Object.defineProperty(previewViewport, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(previewViewport, "clientHeight", {
    configurable: true,
    get: () => 144,
  });
  previewViewport.scrollTop = 256;
  fireEvent.wheel(previewViewport, { deltaY: -40 });
  previewViewport.scrollTop = 120;
  scrollHeight = 520;
  view.rerender(
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={[input]}
        turns={[turn]}
        blocks={[{ id: "t1", kind: "thinking", text: "Step one\nStep two" }]}
        localInputs={[]}
        running
      />
    </PauseConversationFollowing>,
  );
  expect(previewViewport.scrollTop).toBe(120);

  // Scroll back near bottom (520 - 374 - 144 = 2 <= 4) resumes auto-scroll.
  previewViewport.scrollTop = 374;
  fireEvent.scroll(previewViewport);
  scrollHeight = 640;
  view.rerender(
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={[input]}
        turns={[turn]}
        blocks={[
          {
            id: "t1",
            kind: "thinking",
            text: "Step one\nStep two\nStep three",
          },
        ]}
        localInputs={[]}
        running
      />
    </PauseConversationFollowing>,
  );
  expect(previewViewport.scrollTop).toBe(640);

  // Manually expanding ExecutionDetails pauses follow, hides standalone preview (no duplicate content), and opens Work log.
  await user.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(pauseFollowing).toHaveBeenCalledTimes(1);
  const reader = screen.getByRole("region", { name: "Work log" });
  expect(
    screen.getAllByRole("region", { name: "Thought content" }),
  ).toHaveLength(1);
  const details = within(reader).getByText("Thinking").closest("details")!;
  expect(details.open).toBe(true);

  // Manually collapsing active thinking inside Work log keeps it collapsed across updates.
  await user.click(within(details).getByText("Thinking"));
  expect(details.open).toBe(false);
  view.rerender(
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={[input]}
        turns={[turn]}
        blocks={[
          {
            id: "t1",
            kind: "thinking",
            text: "Step one\nStep two\nStep three\nStep four",
          },
        ]}
        localInputs={[]}
        running
      />
    </PauseConversationFollowing>,
  );
  expect(details.open).toBe(false);

  // Manually re-expanding keeps it open across completion and live-to-saved cutover.
  await user.click(within(details).getByText("Thinking"));
  expect(details.open).toBe(true);

  // Collapsing ExecutionDetails while still running returns cleanly to standalone live preview.
  await user.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(
    screen.getAllByRole("region", { name: "Thought content" }),
  ).toHaveLength(1);

  // Re-expanding pins ExecutionDetails open across completion.
  await user.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(pauseFollowing).toHaveBeenCalledTimes(2);
  view.rerender(
    <PauseConversationFollowing value={pauseFollowing}>
      <ConversationTranscript
        threadId="one"
        entries={[
          input,
          {
            position: 1,
            message_kind: "response",
            parts: [
              {
                kind: "thinking",
                text: "Step one\nStep two\nStep three\nStep four",
              },
              { kind: "assistant", text: "All set." },
            ],
          },
        ]}
        turns={[{ ...turn, end_position: 2, final_position: 1 }]}
        blocks={[]}
        localInputs={[]}
        running={false}
      />
    </PauseConversationFollowing>,
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(details.open).toBe(true);
});

it("never auto-opens mobile modal or renders inline thinking preview during an active run", () => {
  viewport();
  render(
    <ConversationTranscript
      threadId="one"
      entries={[entries[0]]}
      turns={[
        {
          turn_id: "mobile-live",
          input_position: 0,
          end_position: 1,
          final_position: null,
          preview: "Investigate scrolling",
        },
      ]}
      blocks={[
        { id: "t1", kind: "thinking", text: "Mobile streaming thought" },
      ]}
      localInputs={[]}
      running
    />,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(screen.queryByText("Mobile streaming thought")).toBeNull();
  expect(screen.getByRole("button", { name: /Work log/ }).textContent).toBe(
    "ThinkingWork log",
  );
});

it("keeps historical or reloaded unfinished runs collapsed without live preview on desktop", () => {
  viewport()(false);
  render(
    <ConversationTranscript
      threadId="one"
      entries={[entries[0]]}
      turns={[
        {
          turn_id: "reloaded-turn",
          input_position: 0,
          end_position: 1,
          final_position: null,
          preview: "Investigate scrolling",
        },
      ]}
      blocks={[{ id: "t1", kind: "thinking", text: "Reloaded thinking" }]}
      localInputs={[]}
      running={false}
    />,
  );
  expect(
    screen
      .getByRole("button", { name: /Work log/ })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
  expect(screen.queryByText("Reloaded thinking")).toBeNull();
});

it("auto-expands live thinking in active LiveOutput and collapses it when a tool arrives", () => {
  const view = render(
    <LiveOutput
      running
      gap={false}
      blocks={[{ id: "sub-t1", kind: "thinking", text: "Subagent plan" }]}
    />,
  );
  const details = screen.getByText("Thinking").closest("details")!;
  expect(details.open).toBe(true);

  view.rerender(
    <LiveOutput
      running
      gap={false}
      blocks={[
        { id: "sub-t1", kind: "thinking", text: "Subagent plan" },
        { id: "sub-tool", kind: "tool", name: "view", text: "{}" },
      ]}
    />,
  );
  expect(details.open).toBe(false);
  expect(within(details).getByText("Thought")).toBeTruthy();
  expect(within(details).queryByText("Thinking")).toBeNull();
});

it("keeps Thinking header and live preview in lockstep after an empty-string tool result and while an earlier tool is running", () => {
  viewport()(false);
  const input: Schema<"TranscriptEntry"> = {
    position: 0,
    message_kind: "request",
    parts: [{ kind: "user", text: "Apply the edit and verify" }],
  };
  const turn = {
    turn_id: "lockstep-turn",
    input_position: 0,
    end_position: 1,
    final_position: null,
    preview: "Apply the edit and verify",
  };
  const renderTranscript = (blocks: DisplayBlock[]) => (
    <ConversationTranscript
      threadId="one"
      entries={[input]}
      turns={[turn]}
      blocks={blocks}
      localInputs={[]}
      running
    />
  );

  // Completed tool returning empty string "" followed by active streaming thinking
  // shows the single Thinking header and live preview underneath.
  const view = render(
    renderTranscript([
      {
        id: "edit-1",
        kind: "tool",
        name: "edit_file",
        toolCallId: "edit-1",
        text: '{"Path":"checkout.ts"}',
        result: "",
        done: true,
        stopped: true,
        outcome: "success",
      },
      {
        id: "thought-1",
        kind: "thinking",
        text: "Verifying the applied diff.",
      },
    ]),
  );
  const trigger = screen.getByRole("button", { name: /Work log/ });
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(
    within(screen.getByRole("region", { name: "Thought content" })).getByText(
      "Verifying the applied diff.",
    ),
  ).toBeTruthy();

  // Earlier tool still running while a trailing thinking block actively streams:
  // header says Thinking and live preview stays visible in lockstep.
  view.rerender(
    renderTranscript([
      {
        id: "cmd-1",
        kind: "tool",
        name: "shell_exec",
        toolCallId: "cmd-1",
        text: '{"command":"pnpm test"}',
        done: true,
      },
      {
        id: "thought-2",
        kind: "thinking",
        text: "Reviewing test progress while command runs.",
      },
    ]),
  );
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(
    within(screen.getByRole("region", { name: "Thought content" })).getByText(
      "Reviewing test progress while command runs.",
    ),
  ).toBeTruthy();

  // Once the trailing thinking finishes while the earlier tool is still running,
  // live preview disappears and the trigger returns to the running tool overview.
  view.rerender(
    renderTranscript([
      {
        id: "cmd-1",
        kind: "tool",
        name: "shell_exec",
        toolCallId: "cmd-1",
        text: '{"command":"pnpm test"}',
        done: true,
      },
      {
        id: "thought-2",
        kind: "thinking",
        text: "Reviewing test progress while command runs.",
        done: true,
      },
    ]),
  );
  expect(trigger.textContent).toContain("pnpm test");
  expect(trigger.textContent).not.toContain("Thinking");
  expect(screen.queryByRole("region", { name: "Thought content" })).toBeNull();
});

it("streams live thinking preview directly from FocusDisplay after an empty-string displayItem tool result", () => {
  viewport()(false);
  const display = new FocusDisplay();
  display.accept(
    focusFrame({
      kind: "snapshot",
      resume_cursor: null,
      snapshot: {
        epoch: "epoch-one",
        cutover_sequence: 100,
        thread: {
          thread: {
            thread_id: "one",
            created_at: "2026-11-26",
            updated_at: "2026-11-26",
            metadata_version: 1,
            archived: false,
            configuration: {
              version: 1,
              agent_source: { kind: "acp_agent", id: "acp-one" },
              environment_profile_id: "local",
            },
            continuation_state: "initial",
            root_activity: { state: "running", run_id: "run-one" },
          },
          continuation_id: null,
        },
        children: { executions: [], total: 0 },
        recent_events: [],
        root_stream: {
          thread_id: "one",
          run_id: "run-one",
          base_continuation_id: null,
          event_count: 0,
        },
      },
    }),
  );
  display.accept(focusFrame({ kind: "ready", resume_cursor: "ready" }));
  let position = 0;
  const emit = (
    id: string,
    kind: "tool_call" | "reasoning_message",
    state: "in_progress" | "completed",
    payload: Record<string, unknown>,
  ) => {
    position++;
    display.accept(
      focusFrame({
        kind: "event",
        resume_cursor: `cursor-${position}`,
        event: {
          sequence: 100 + position,
          epoch: "epoch-one",
          run_kind: "root",
          thread_id: "one",
          root_thread_id: "one",
          run_id: "run-one",
          event_type: String(payload.type),
          payload,
          payload_omitted: false,
          display_position: { attempt: 1, sequence: position },
          item: { id, kind, state },
        },
      }),
    );
  };

  emit("tool-1", "tool_call", "in_progress", {
    type: "TOOL_CALL_START",
    toolCallId: "tool-1",
    toolCallName: "edit_file",
  });
  emit("tool-1", "tool_call", "in_progress", {
    type: "TOOL_CALL_ARGS",
    toolCallId: "tool-1",
    delta: '{"Path":"checkout.ts"}',
  });
  emit("tool-1", "tool_call", "in_progress", {
    type: "TOOL_CALL_END",
    toolCallId: "tool-1",
  });
  emit("tool-1", "tool_call", "completed", {
    type: "TOOL_CALL_RESULT",
    toolCallId: "tool-1",
    messageId: "tool-1:result",
    content: "",
  });
  emit("thought-1", "reasoning_message", "in_progress", {
    type: "REASONING_MESSAGE_START",
    messageId: "thought-1",
    role: "reasoning",
  });
  emit("thought-1", "reasoning_message", "in_progress", {
    type: "REASONING_MESSAGE_CONTENT",
    messageId: "thought-1",
    delta: "Checking retry idempotency after applying edit.",
  });

  render(
    <ConversationTranscript
      threadId="one"
      entries={[
        {
          position: 0,
          message_kind: "request",
          parts: [{ kind: "user", text: "Fix checkout.ts" }],
        },
      ]}
      turns={[
        {
          turn_id: "acp-turn",
          input_position: 0,
          end_position: 1,
          final_position: null,
          preview: "Fix checkout.ts",
        },
      ]}
      blocks={display.blocksAfter(null)}
      localInputs={[]}
      running
    />,
  );
  const trigger = screen.getByRole("button", { name: /Work log/ });
  expect(trigger.textContent).toBe("ThinkingWork log");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(
    within(screen.getByRole("region", { name: "Thought content" })).getByText(
      "Checking retry idempotency after applying edit.",
    ),
  ).toBeTruthy();
});
