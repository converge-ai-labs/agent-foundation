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
import type { Schema } from "../transport/client";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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
  expect(
    screen.queryByRole("region", { name: "Execution details" }),
  ).toBeNull();
  expect(screen.queryByText("A long internal plan")).toBeNull();
  const trigger = screen.getByRole("button", { name: /Execution details/ });
  const user = userEvent.setup();
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", {
    name: "Execution details",
  });
  expect(within(dialog).getByText("A long internal plan")).toBeTruthy();
  expect(within(dialog).queryByText("Investigate scrolling")).toBeNull();
  expect(document.querySelectorAll("[data-execution-reader]")).toHaveLength(1);
  view.rerender(transcript(true));
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(within(dialog).getByText("Completed")).toBeTruthy();
  expect(within(dialog).queryByText("The final answer")).toBeNull();
  await user.click(
    within(dialog).getByRole("button", { name: "Back to conversation" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    screen.getByText("The final answer").closest("[data-execution-reader]"),
  ).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

it("retains inner disclosure state and reader identity across visits and supports Escape", async () => {
  viewport();
  render(transcript());
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: /Execution details/ });
  await user.click(trigger);
  const reader = await screen.findByRole("region", {
    name: "Execution details",
  });
  const reasoning = screen
    .getByText("A long internal plan")
    .closest("details")!;
  fireEvent.click(within(reader).getByText("Reasoning"));
  expect(reasoning.open).toBe(false);
  reader.focus();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(trigger);
  expect(await screen.findByRole("region", { name: "Execution details" })).toBe(
    reader,
  );
  expect(reasoning.open).toBe(false);
});

it("switches between mobile inspection and the existing desktop disclosure", async () => {
  const resize = viewport();
  render(transcript());
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Execution details/ }));
  await screen.findByRole("dialog");
  resize(false);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "Execution details" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  resize(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    screen.queryByRole("region", { name: "Execution details" }),
  ).toBeNull();
});
