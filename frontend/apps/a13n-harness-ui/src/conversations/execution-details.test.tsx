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
import type { DisplayBlock } from "./stream";
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
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(
    within(screen.getByRole("region", { name: "Work log" }))
      .getByText("Thinking")
      .closest("details")?.open,
  ).toBe(true);
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

it("auto-expands active desktop execution and tails live thinking without pausing conversation follow, then collapses on tool and completion", async () => {
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

  // Empty thinking START frame opens desktop execution stretch without flashing an empty thought box.
  const view = render(
    renderTranscript([{ id: "t1", kind: "thinking", text: "   " }]),
  );
  const trigger = screen.getByRole("button", { name: /Work log/ });
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(pauseFollowing).not.toHaveBeenCalled();
  const reader = screen.getByRole("region", { name: "Work log" });
  expect(within(reader).getByText("Thinking").closest("details")?.open).toBe(
    false,
  );

  // First non-empty delta auto-opens Thinking and scrolls viewport to bottom.
  view.rerender(
    renderTranscript([{ id: "t1", kind: "thinking", text: "First step" }]),
  );
  const firstDetails = within(reader).getByText("Thinking").closest("details")!;
  expect(firstDetails.open).toBe(true);
  const thoughtViewport = within(firstDetails).getByRole("region", {
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

  // Brief gap after REASONING_MESSAGE_END keeps the trailing thought open until the next row arrives.
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
  expect(firstDetails.open).toBe(true);
  expect(within(firstDetails).getByText("Thought")).toBeTruthy();

  // Tool call arrives even if earlier thinking did not flip done: earlier thinking collapses and settles to Thought.
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
  expect(firstDetails.open).toBe(false);
  expect(within(firstDetails).queryByText("Thinking")).toBeNull();
  expect(within(firstDetails).getByText("Thought")).toBeTruthy();
  expect(trigger.textContent).not.toContain("Thinking");

  // Subsequent thinking after tool call auto-expands and scrolls live.
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
  expect(firstDetails.open).toBe(false);
  const secondDetails = within(reader)
    .getByText("Thinking")
    .closest("details")!;
  expect(secondDetails.open).toBe(true);

  // Assistant prose follows: ExecutionDetails auto-collapses without pausing follow.
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
  expect(pauseFollowing).not.toHaveBeenCalled();

  // Turn completes: collapses behind Worked for ... and stays collapsed.
  view.rerender(renderTranscript([], false, true));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(trigger.textContent).toContain("Worked for 8s");
});

it("respects manual overrides and pauses thinking auto-scroll when the viewer scrolls up", async () => {
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
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  const reader = screen.getByRole("region", { name: "Work log" });
  const details = within(reader).getByText("Thinking").closest("details")!;
  expect(details.open).toBe(true);

  // Scroll up pauses auto-scroll; scrolling back within 4px resumes auto-scroll.
  const thoughtViewport = within(details).getByRole("region", {
    name: "Thought content",
  });
  let scrollHeight = 400;
  Object.defineProperty(thoughtViewport, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(thoughtViewport, "clientHeight", {
    configurable: true,
    get: () => 144,
  });
  thoughtViewport.scrollTop = 256;
  fireEvent.wheel(thoughtViewport, { deltaY: -40 });
  thoughtViewport.scrollTop = 120;
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
  expect(thoughtViewport.scrollTop).toBe(120);

  // Scroll back near bottom (520 - 374 - 144 = 2 <= 4) resumes auto-scroll.
  thoughtViewport.scrollTop = 374;
  fireEvent.scroll(thoughtViewport);
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
  expect(thoughtViewport.scrollTop).toBe(640);

  // Manually collapsing active thinking keeps it collapsed across updates.
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

  // Manually expanding keeps it open across subsequent rows and live-to-saved cutover.
  await user.click(within(details).getByText("Thinking"));
  expect(details.open).toBe(true);

  // Manually collapsing and re-expanding ExecutionDetails pins it open across completion and calls pauseFollowing on expand.
  await user.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(pauseFollowing).not.toHaveBeenCalled();
  await user.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(pauseFollowing).toHaveBeenCalledTimes(1);

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

it("never auto-opens mobile modal or inline thinking preview during an active run", () => {
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
  expect(screen.queryByText("Mobile streaming thought")).toBeNull();
  expect(screen.getByRole("button", { name: /Work log/ }).textContent).toBe(
    "ThinkingWork log",
  );
});

it("keeps historical or reloaded unfinished runs collapsed by default on desktop", () => {
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
