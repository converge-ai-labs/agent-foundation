// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import type { Schema } from "../transport/client";
import { inputCopyText, InputContent } from "./input-content";
import { ConversationTranscript } from "./transcript";

afterEach(cleanup);

const parts: Schema<"TranscriptPart">[] = [
  {
    kind: "user",
    text: "Task from Thread thread-origin. Internal collaboration instructions.",
    metadata: { display: false },
  },
  {
    kind: "user",
    text: "Review the implementation and report the findings.",
    metadata: {
      harness_ui: {
        thread_message: {
          source_thread_id: "thread-origin",
          source_thread_title: "Release review",
        },
      },
    },
  },
];

it.each(["live", "saved"])(
  "shows attributed %s input without the template and navigates to its source",
  async (mode) => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/threads/thread-worker"]}>
        <Routes>
          <Route
            path="/threads/thread-worker"
            element={
              <ConversationTranscript
                threadId="thread-worker"
                entries={
                  mode === "saved"
                    ? [{ position: 0, message_kind: "request", parts }]
                    : []
                }
                blocks={
                  mode === "live"
                    ? parts.map((part, index) => ({
                        id: `run-worker:input:0:${index}`,
                        kind: "user" as const,
                        text: part.text!,
                        metadata: part.metadata!,
                      }))
                    : []
                }
                localInputs={[]}
              />
            }
          />
          <Route
            path="/threads/thread-origin"
            element={<h1>Source conversation</h1>}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(parts[1].text!)).toBeTruthy();
    expect(screen.queryByText(parts[0].text!)).toBeNull();
    expect(screen.queryByText("User")).toBeNull();
    const link = screen.getByRole("link", {
      name: "From thread Release review",
    });
    expect(link.getAttribute("href")).toBe("/threads/thread-origin");
    expect(inputCopyText(parts)).toBe(parts[1].text);
    await user.click(link);
    expect(
      screen.getByRole("heading", { name: "Source conversation" }),
    ).toBeTruthy();
  },
);

it("uses the source ID without a title and does not infer provenance from ordinary text", () => {
  const body = {
    ...parts[1],
    metadata: {
      harness_ui: { thread_message: { source_thread_id: "thread-untitled" } },
    },
  };
  const view = render(
    <MemoryRouter>
      <InputContent parts={[body]} renderText={(text) => text} />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: "From thread thread-untitled" }),
  ).toBeTruthy();
  const legacy = { kind: "user", text: parts[0].text };
  view.rerender(
    <MemoryRouter>
      <InputContent parts={[legacy]} renderText={(text) => text} />
    </MemoryRouter>,
  );
  expect(screen.getByText("User")).toBeTruthy();
  expect(screen.getByText(legacy.text!)).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  expect(inputCopyText([legacy])).toBe(legacy.text);
});
