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
    expect(screen.queryByRole("button", { name: "Copy message" })).toBeNull();
    const disclosure = screen.getByRole("button", {
      name: "Thread message details",
      expanded: false,
    });
    expect(screen.getByText("Show message")).toBeTruthy();
    const content = document.getElementById(
      disclosure.getAttribute("aria-controls")!,
    )!;
    expect(content.hidden).toBe(true);
    await user.click(screen.getByText(parts[1].text!));
    expect(screen.getByText(parts[1].text!)).toBeTruthy();
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(content.hidden).toBe(false);
    expect(screen.getByText("Show less")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy message" })).toBeTruthy();
    await user.keyboard(" ");
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    expect(content.hidden).toBe(true);
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
  expect(
    screen.queryByRole("button", { name: "Thread message details" }),
  ).toBeNull();
  expect(inputCopyText([legacy])).toBe(legacy.text);
});

it("reveals rich content only after keyboard expansion and copies the complete message", async () => {
  const user = userEvent.setup();
  const text = `${"Review the implementation carefully. ".repeat(20)}\n\nRead the [review notes](https://example.com/review).`;
  render(
    <MemoryRouter>
      <ConversationTranscript
        threadId="thread-worker"
        entries={[
          {
            position: 0,
            message_kind: "request",
            parts: [parts[0], { ...parts[1], text }],
          },
        ]}
        blocks={[]}
        localInputs={[]}
      />
    </MemoryRouter>,
  );
  expect(screen.queryByRole("link", { name: "review notes" })).toBeNull();
  await user.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("link", { name: "From thread Release review" }),
  );
  await user.tab();
  const disclosure = screen.getByRole("button", {
    name: "Thread message details",
    expanded: false,
  });
  expect(document.activeElement).toBe(disclosure);
  await user.keyboard("{Enter}");
  expect(screen.getByRole("link", { name: "review notes" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Copy message" }));
  expect(await navigator.clipboard.readText()).toBe(text);
  await user.click(disclosure);
  expect(screen.queryByRole("link", { name: "review notes" })).toBeNull();
  expect(screen.queryByText(parts[0].text!)).toBeNull();
});
