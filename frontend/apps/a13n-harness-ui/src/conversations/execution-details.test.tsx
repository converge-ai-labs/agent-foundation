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
import { ConversationTranscript } from "./transcript";
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

it("folds completed progress, thoughts and tools together while keeping the final response visible", async () => {
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
  expect(screen.getByText("I will inspect the submit handler.")).toBeTruthy();
  view.rerender(<ConversationTranscript {...props} />);
  const trigger = screen.getByRole("button", {
    name: /Worked for 1m 5s/,
    expanded: false,
  });
  expect(screen.queryByText("I will inspect the submit handler.")).toBeNull();
  expect(screen.queryByText("The handler needs a guard.")).toBeNull();
  expect(
    screen.getByText("The final answer").closest("[data-execution-reader]"),
  ).toBeNull();
  const user = userEvent.setup();
  await user.click(trigger);
  const reader = screen.getByRole("region", { name: "Work log" });
  expect(
    within(reader).getByText("I will inspect the submit handler."),
  ).toBeTruthy();
  expect(within(reader).getByText("The handler needs a guard.")).toBeTruthy();
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

it("keeps an explicitly opened reader when completion gathers the preceding progress into it", async () => {
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
  expect(within(reader).getByText("Looking into this now.")).toBeTruthy();
  expect(
    screen.getByText("Finished.").closest("[data-execution-reader]"),
  ).toBeNull();
});
