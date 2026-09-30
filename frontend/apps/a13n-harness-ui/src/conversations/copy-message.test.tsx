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

it("copies all final Markdown parts once, excluding execution details and resumed live output", async () => {
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
      blocks={[
        { id: "live", kind: "assistant", text: "Resumed progress", done: true },
      ]}
    />,
  );
  const buttons = screen.getAllByRole("button", { name: "Copy message" });
  expect(buttons).toHaveLength(2);
  fireEvent.click(buttons[1]);
  await screen.findByRole("button", { name: "Copied" });
  expect(write).toHaveBeenCalledWith(
    "# Final\n\n**Exact** source\n\nSecond [part](https://example.com)",
  );
  expect(screen.queryByText("Thinking")).toBeNull();
  for (const toggle of screen.getAllByRole("button", {
    name: /Execution details/,
  }))
    fireEvent.click(toggle);
  for (const text of ["Progress update", "Thinking", "Resumed progress"]) {
    const section = screen.getByText(text).closest("section, details")!;
    expect(
      within(section as HTMLElement).queryByRole("button", {
        name: /Copy message|Copied/,
      }),
    ).toBeNull();
  }
  view.rerender(
    <LiveOutput
      blocks={[
        { id: "live", kind: "assistant", text: "Not saved", done: true },
      ]}
      gap={false}
    />,
  );
  expect(screen.queryByRole("button", { name: /Copy message/ })).toBeNull();
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
