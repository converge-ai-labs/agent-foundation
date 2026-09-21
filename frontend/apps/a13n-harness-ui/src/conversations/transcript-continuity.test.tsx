// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import type { DisplayBlock } from "./stream";
import { ConversationTranscript } from "./transcript";
import { useHistory } from "./queries";
import { previewInput, type LocalInput } from "./local-input";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const local: LocalInput = {
  id: "round",
  action: "send",
  state: "accepted",
  parts: previewInput("round", ["Prompt"]),
};
const input: Schema<"TranscriptEntry"> = {
  position: 0,
  message_kind: "request",
  parts: local.parts as Schema<"TranscriptPart">[],
};
const output: Schema<"TranscriptEntry"> = {
  position: 1,
  message_kind: "response",
  parts: [{ kind: "assistant", text: "Visible reply" }],
};
const turn = {
  turn_id: "round",
  input_position: 0,
  end_position: 1,
  preview: "Prompt",
};
const props = {
  entries: [input],
  turns: [turn],
  localInputs: [],
  threadId: "one",
  continuation: "C0",
};
const readingAnchor = (node: HTMLElement) =>
  node.closest<HTMLElement>("[data-reading-anchor]")?.dataset.readingAnchor;

it("retains live output nodes and reading anchors through repeated saves while updating exact comment targets", () => {
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[
        { id: "run:message-1", kind: "assistant", text: "Visible reply" },
      ]}
    />,
  );
  const before = screen.getByText("Visible reply");
  const anchor = readingAnchor(before);
  for (const continuation of ["C1", "C2"]) {
    const target: Schema<"SavedOutputTarget"> = {
      producing_thread_id: "one",
      source_id: continuation,
      location: { kind: "root_text", message: 1, part: 0 },
    };
    view.rerender(
      <ConversationTranscript
        {...props}
        entries={[
          input,
          {
            ...output,
            parts: [{ ...output.parts[0], comment_target: target }],
          },
        ]}
        turns={[{ ...turn, end_position: 2 }]}
        blocks={[]}
        continuation={continuation}
      />,
    );
    expect(screen.getByText("Visible reply")).toBe(before);
    expect(readingAnchor(before)).toBe(anchor);
    expect(
      JSON.parse(
        before.closest<HTMLElement>("[data-saved-target]")!.dataset
          .savedTarget!,
      ),
    ).toEqual(target);
  }
});

it.each([false, true])(
  "retains first-turn content and the open execution reader during slow multi-page saving (mobile=%s)",
  async (mobile) => {
    vi.stubGlobal("matchMedia", () => ({
      matches: mobile,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const queries = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const plan: Schema<"TranscriptEntry"> = {
      position: 1,
      message_kind: "response",
      parts: [{ kind: "thinking", text: "Plan" }],
    };
    const reply = { ...output, position: 2 };
    const savedTurn = { ...turn, end_position: 3 };
    const blocks: DisplayBlock[] = [
      {
        id: "round:input:0",
        kind: "user",
        text: "Prompt",
        metadata: { source_id: "round" },
      },
      { id: "run:plan", kind: "thinking", text: "Plan" },
      { id: "run:reply", kind: "assistant", text: "Visible reply" },
    ];
    let finish!: (value: unknown) => void;
    const GET = vi
      .fn()
      .mockResolvedValueOnce({
        data: {
          continuation_id: "C0",
          entries: [],
          turns: [],
          next_cursor: null,
        },
      })
      .mockResolvedValueOnce({
        data: {
          continuation_id: "C1",
          entries: [reply],
          boundary_entries: [input],
          turns: [savedTurn],
          next_cursor: "older",
          earlier_turns_cursor: null,
        },
      })
      .mockResolvedValueOnce({
        data: { entries: [reply], next_cursor: "older" },
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    function Transcript({ continuation }: { continuation: string }) {
      const history = useHistory("one", continuation, true);
      const page = history.data?.pages[0];
      return (
        <ConversationTranscript
          threadId="one"
          localInputs={[local]}
          loadDetails
          entries={page?.entries ?? []}
          turns={page?.turns ?? []}
          blocks={page?.continuation_id === "C1" ? [] : blocks}
          continuation={page?.continuation_id}
        />
      );
    }
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queries}>
        <TransportContext value={{ client: { GET } } as unknown as Transport}>
          {children}
        </TransportContext>
      </QueryClientProvider>
    );
    const view = render(<Transcript continuation="C0" />, { wrapper });
    await waitFor(() =>
      expect(
        queries.getQueryData(["thread", "one", "history", "C0"]),
      ).toBeTruthy(),
    );
    const toggle = screen.getByRole("button", { name: /Execution details/ });
    fireEvent.click(toggle);
    const reader = screen.getByRole("region", { name: "Execution details" });
    const prompt = screen.getByText("Prompt");
    const reasoning = screen.getByText("Plan");
    const answer = screen.getByText("Visible reply");
    const anchor = readingAnchor(answer);
    const assertRetained = () => {
      expect(screen.queryByText("Loading turn…")).toBeNull();
      expect(screen.getByText("Prompt")).toBe(prompt);
      expect(screen.getByText("Plan")).toBe(reasoning);
      expect(screen.getByText("Visible reply")).toBe(answer);
      expect(readingAnchor(answer)).toBe(anchor);
      expect(screen.getByRole("region", { name: "Execution details" })).toBe(
        reader,
      );
      expect(toggle.isConnected).toBe(true);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
    };
    view.rerender(<Transcript continuation="C1" />);
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(4));
    assertRetained();
    await act(async () =>
      finish({ data: { entries: [input, plan], next_cursor: null } }),
    );
    await waitFor(() =>
      expect(
        queries.getQueryData(["thread", "one", "history", "C1"]),
      ).toBeTruthy(),
    );
    assertRetained();
    expect(GET).toHaveBeenCalledTimes(4);
    view.unmount();
    queries.clear();
  },
);

it("preserves distinct repeated text slots without merging messages", () => {
  const blocks: DisplayBlock[] = [
    { id: "run:first", kind: "assistant", text: "Repeated" },
    { id: "run:plan", kind: "thinking", text: "Plan" },
    { id: "run:second", kind: "assistant", text: "Repeated" },
  ];
  const view = render(<ConversationTranscript {...props} blocks={blocks} />);
  const before = screen.getAllByText("Repeated");
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        {
          ...output,
          parts: [
            { kind: "assistant", text: "Repeated" },
            { kind: "thinking", text: "Plan" },
            { kind: "assistant", text: "Repeated" },
          ],
        },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  const after = screen.getAllByText("Repeated");
  expect(after).toHaveLength(2);
  expect(after[0]).toBe(before[0]);
  expect(after[1]).toBe(before[1]);
  expect(readingAnchor(after[0])).not.toBe(readingAnchor(after[1]));
});

it("does not remount an unchanged saved part when a sibling changes, but replaces changed saved text", () => {
  const entries = [
    input,
    {
      ...output,
      parts: [
        { kind: "assistant" as const, text: "Visible reply" },
        { kind: "thinking" as const, text: "First plan" },
      ],
    },
  ];
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={entries}
      turns={[{ ...turn, end_position: 2 }]}
    />,
  );
  const answer = screen.getByText("Visible reply");
  fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
  const reasoning = screen.getByText("Reasoning").closest("details")!;
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        {
          ...output,
          parts: [output.parts[0], { kind: "thinking", text: "Changed plan" }],
        },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  expect(screen.getByText("Visible reply")).toBe(answer);
  expect(screen.getByText("Reasoning").closest("details")).not.toBe(reasoning);
});

it("does not transfer live state to changed saved text or to another input boundary", () => {
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[{ id: "run:reply", kind: "assistant", text: "Visible reply" }]}
    />,
  );
  const answer = screen.getByText("Visible reply");
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[
        input,
        { ...output, parts: [{ kind: "assistant", text: "Different reply" }] },
      ]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  expect(screen.getByText("Different reply")).not.toBe(answer);
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[{ id: "run:new", kind: "assistant", text: "Visible reply" }]}
    />,
  );
  const next = screen.getByText("Visible reply");
  const other = {
    ...input,
    parts: previewInput("other", [
      "Other prompt",
    ]) as Schema<"TranscriptPart">[],
  };
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={[]}
      entries={[other, output]}
      turns={[{ ...turn, turn_id: "other", end_position: 2 }]}
      continuation="C2"
    />,
  );
  expect(screen.getByText("Visible reply")).not.toBe(next);
});

it("never assigns a still-live message identity to matching saved text", () => {
  const blocks: DisplayBlock[] = [
    { id: "run:reply", kind: "assistant", text: "Visible reply" },
  ];
  const view = render(<ConversationTranscript {...props} blocks={blocks} />);
  view.rerender(
    <ConversationTranscript
      {...props}
      blocks={blocks}
      entries={[input, output]}
      turns={[{ ...turn, end_position: 2 }]}
      continuation="C1"
    />,
  );
  const messages = screen.getAllByText("Visible reply");
  expect(messages).toHaveLength(2);
  expect(readingAnchor(messages[0])).not.toBe(readingAnchor(messages[1]));
});
