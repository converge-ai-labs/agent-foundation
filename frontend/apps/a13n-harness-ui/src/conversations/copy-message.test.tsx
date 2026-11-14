// @vitest-environment jsdom
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
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MessageBranches } from "./message-branch";
import { CopyMessage } from "./copy-message";
import { InputContent, inputCopyText, type InputPart } from "./input-content";
import { previewInput } from "./local-input";
import { ConversationTranscript, LiveOutput } from "./transcript";
import type { Schema } from "../transport/client";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  return writeText;
}

it("copies exact source, announces success, and resets the same button", async () => {
  vi.useFakeTimers();
  const write = clipboard();
  const text = "  **Keep**\n\n```ts\nconst x = 1;\n```\n";
  render(<CopyMessage text={text} />);
  const button = screen.getByRole("button", { name: "Copy message" });
  await act(async () => fireEvent.click(button));
  expect(write).toHaveBeenCalledWith(text);
  expect(
    screen.getAllByRole("button", { name: /Copy message|Copied/ }),
  ).toEqual([button]);
  expect(screen.getByRole("button", { name: "Copied" })).toBe(button);
  expect(
    screen.getByText("Copied to clipboard").getAttribute("aria-live"),
  ).toBe("polite");
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByRole("button", { name: "Copy message" })).toBe(button);
});

it("supports keyboard activation", async () => {
  const user = userEvent.setup();
  const write = clipboard();
  render(<CopyMessage text="Keyboard source" />);
  await user.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Copy message" }),
  );
  await user.keyboard("{Enter}");
  expect(write).toHaveBeenCalledWith("Keyboard source");
  expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
});

it("reports unavailable clipboard access and permits a deliberate retry", async () => {
  vi.stubGlobal("navigator", {});
  render(<CopyMessage text="Retry source" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  const write = clipboard(
    vi
      .fn()
      .mockRejectedValueOnce(new Error("Permission denied"))
      .mockResolvedValue(undefined),
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  expect(write).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("does not announce success before the clipboard write resolves", async () => {
  let resolve!: () => void;
  const write = clipboard(
    vi.fn(
      () =>
        new Promise<void>((done) => {
          resolve = done;
        }),
    ),
  );
  render(<CopyMessage text="Pending" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button"));
  expect(write).toHaveBeenCalledTimes(1);
  await act(async () => resolve());
  expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
});

it("preserves authored whitespace and repeated attachment positions without expanded or hidden content", () => {
  const attachment: Schema<"ThreadAttachment"> = {
    attachment_id: "attachment-one",
    name: "notes.txt",
    media_type: "text/plain",
    size: 12,
    source: null,
  };
  const parts = previewInput(
    "input-one",
    [
      "  Before\n",
      { attachment_id: attachment.attachment_id },
      " and ",
      { attachment_id: attachment.attachment_id },
      "\nafter  ",
    ],
    new Map([[attachment.attachment_id, attachment]]),
  );
  const expanded: InputPart[] = [
    parts[0],
    { ...parts[1], text: "Internal path and captured file text" },
    { ...parts[1], kind: "media", text: "Internal media payload" },
    parts[2],
    parts[3],
    parts[4],
    { kind: "user", text: "Injected context", metadata: { display: false } },
    { kind: "system", text: "System context" },
  ];
  expect(inputCopyText(expanded)).toBe(
    "  Before\n[notes.txt] and [notes.txt]\nafter  ",
  );
  expect(inputCopyText([parts[1]])).toBe("[notes.txt]");
  expect(
    inputCopyText([
      { kind: "user", text: "First" },
      { kind: "user", text: "Second" },
    ]),
  ).toBe("First\n\nSecond");
});

it("offers one copy control for submitted input, but none for empty or hidden input", async () => {
  const write = clipboard();
  const view = render(
    <InputContent
      parts={previewInput("one", ["  Prompt\nbody  "])}
      renderText={(text) => text}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(write).toHaveBeenCalledWith("  Prompt\nbody  ");
  view.rerender(
    <InputContent
      parts={[
        { kind: "user", text: " ", metadata: {} },
        { kind: "user", text: "Hidden", metadata: { display: false } },
      ]}
      renderText={(text) => text}
    />,
  );
  expect(screen.queryByRole("button")).toBeNull();
});

function entry(
  position: number,
  parts: Schema<"TranscriptPart">[],
): Schema<"TranscriptEntry"> {
  return { position, message_kind: "response", parts };
}

it.each(["desktop", "narrow"])(
  "copies all final Markdown parts once, excluding completed execution details on %s",
  async (layout) => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: layout === "narrow" && query === "(max-width: 700px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const write = clipboard();
    const view = render(
      <ConversationTranscript
        threadId="one"
        localInputs={[]}
        turns={[
          {
            turn_id: "turn",
            input_position: 0,
            end_position: 3,
            output_position: 2,
            final_position: 2,
            preview: "Prompt",
          },
        ]}
        entries={[
          entry(0, [{ kind: "user", text: "Prompt" }]),
          entry(1, [
            { kind: "assistant", text: "Progress update" },
            { kind: "thinking", text: "Thinking" },
          ]),
          entry(2, [
            { kind: "assistant", text: "# Final\n\n**Exact** source" },
            { kind: "assistant", text: "Second [part](https://example.com)" },
          ]),
        ]}
        blocks={[]}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: "Copy message" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]);
    await screen.findByRole("button", { name: "Copied" });
    expect(write).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledWith(
      "# Final\n\n**Exact** source\n\nSecond [part](https://example.com)",
    );
    // Completed progress is not readable until the execution disclosure opens.
    expect(screen.queryByText("Progress update")).toBeNull();
    expect(screen.queryByRole("region", { name: "Work log" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    const reader = await screen.findByRole("region", { name: "Work log" });
    expect(within(reader).getByText("Progress update")).toBeTruthy();
    expect(
      within(reader).queryByRole("region", { name: "Thought content" }),
    ).toBeNull();
    fireEvent.click(within(reader).getByRole("button", { name: "Thought" }));
    expect(
      within(reader).getByRole("region", { name: "Thought content" })
        .textContent,
    ).toContain("Thinking");
    expect(
      within(reader).queryByRole("button", {
        name: /Copy message|Copied/,
        hidden: true,
      }),
    ).toBeNull();
    if (layout === "narrow") {
      fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    }
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: /Copy message|Copied/ }),
      ).toHaveLength(2),
    );
    view.unmount();
  },
);

it("keeps resumed progress readable without offering to copy unsaved output", () => {
  const view = render(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      turns={[
        {
          turn_id: "turn",
          input_position: 0,
          end_position: 3,
          output_position: 2,
          preview: "Prompt",
        },
      ]}
      entries={[
        entry(0, [{ kind: "user", text: "Prompt" }]),
        entry(1, [{ kind: "assistant", text: "Progress update" }]),
        entry(2, [{ kind: "assistant", text: "Legacy closing output" }]),
      ]}
      blocks={[
        { id: "live", kind: "assistant", text: "Resumed progress", done: true },
      ]}
    />,
  );
  // An output position is not successful completion, and done live blocks are
  // not saved history. Neither should cause resumed progress to disappear.
  expect(
    screen.getByText("Progress update").closest("[data-execution-reader]"),
  ).toBeNull();
  const resumed = screen
    .getByText("Resumed progress")
    .closest<HTMLElement>("[data-reading-anchor]");
  expect(resumed).toBeTruthy();
  expect(
    within(resumed!).queryByRole("button", {
      name: /Copy message|Copied/,
      hidden: true,
    }),
  ).toBeNull();
  expect(screen.getAllByRole("button", { name: "Copy message" })).toHaveLength(
    2,
  );
  view.rerender(
    <LiveOutput
      blocks={[
        { id: "live", kind: "assistant", text: "Not saved", done: true },
      ]}
      gap={false}
    />,
  );
  const unsaved = screen.getByRole("region", {
    name: "Current unsaved output",
  });
  expect(within(unsaved).getByText("Not saved")).toBeTruthy();
  expect(
    within(unsaved).queryByRole("button", {
      name: /Copy message|Copied/,
      hidden: true,
    }),
  ).toBeNull();
});

it("only adds final output copying after saved-history cutover and labels truncated previews", async () => {
  const write = clipboard();
  const turn = {
    turn_id: "turn",
    input_position: 0,
    end_position: 2,
    preview: "Prompt",
  };
  const entries = [
    entry(0, [{ kind: "user", text: "Prompt" }]),
    entry(1, [
      { kind: "assistant", text: "Saved preview", text_truncated: true },
    ]),
  ];
  const view = render(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      blocks={[]}
      entries={entries}
      turns={[turn]}
    />,
  );
  expect(screen.getAllByRole("button", { name: "Copy message" })).toHaveLength(
    1,
  );
  view.rerender(
    <ConversationTranscript
      threadId="one"
      localInputs={[]}
      blocks={[]}
      entries={entries}
      turns={[{ ...turn, final_position: 1 }]}
    />,
  );
  expect(
    screen.getByText("Saved preview truncated by the server."),
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Copy available preview" }),
  );
  await screen.findByRole("button", { name: "Copied" });
  expect(write).toHaveBeenCalledWith("Saved preview");
});

it("offers copy, branch and the stop time once after a tool interrupts the last reply", async () => {
  const write = clipboard();
  const create = vi.fn();
  const time = "2026-04-01T12:30:00.000Z";
  render(
    <MessageBranches
      value={{
        busy: false,
        pending: false,
        create,
        points: { turn: { edit: true, branch: true } },
      }}
    >
      <ConversationTranscript
        threadId="one"
        localInputs={[]}
        blocks={[]}
        entries={[
          entry(0, [{ kind: "user", text: "Start" }]),
          entry(1, [{ kind: "assistant", text: "Earlier" }]),
          entry(2, [{ kind: "assistant", text: "Last saved prose" }]),
          entry(3, [{ kind: "thinking", text: "Next step" }]),
        ]}
        turns={[
          {
            turn_id: "turn",
            input_position: 0,
            end_position: 4,
            preview: "Start",
          },
        ]}
        terminalTurn={{ id: "turn", time }}
      />
    </MessageBranches>,
  );
  const footer = screen.getByRole("button", {
    name: "Branch in new chat",
  }).parentElement!;
  fireEvent.click(within(footer).getByRole("button", { name: "Copy message" }));
  expect(write).toHaveBeenCalledWith("Last saved prose");
  expect(footer.querySelector("time")?.dateTime).toBe(time);
  fireEvent.click(
    within(footer).getByRole("button", { name: "Branch in new chat" }),
  );
  expect(create).toHaveBeenCalledWith("turn");
});
