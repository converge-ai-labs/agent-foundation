// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Schema, Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { SavedEntry, LiveOutput, MessageText } from "./transcript";

afterEach(cleanup);
const metadata = {
  harness_ui: {
    attachment: {
      attachment_id: "attachment-one",
      name: "example.png",
      media_type: "image/png",
      size: 12,
      source: null,
    },
  },
};
it("renders saved attachment metadata once while preserving exact model-visible content and output targets", () => {
  const entry = {
    position: 0,
    message_kind: "request",
    parts: [
      { kind: "user", text: "Review this" },
      { kind: "user", text: "Original attachment reference", metadata },
      {
        kind: "media",
        text: JSON.stringify({ kind: "binary", payload_omitted: true }),
        metadata: { ...metadata, media: true },
      },
      { kind: "user", text: "Private guidance", metadata: { display: false } },
    ],
  } as Schema<"TranscriptEntry">;
  render(
    <TransportContext
      value={
        {
          fetch: vi.fn().mockRejectedValue(new Error("Preview unavailable")),
        } as unknown as Transport
      }
    >
      <SavedEntry entry={entry} threadId="thread-one" />
    </TransportContext>,
  );
  expect(screen.getAllByText("example.png")).toHaveLength(1);
  expect(screen.getByText("Original attachment reference")).toBeTruthy();
  expect(screen.queryByText("Private guidance")).toBeNull();
});
it("groups live media with its input turn, not a later instruction reusing the same attachment", () => {
  render(
    <TransportContext
      value={
        {
          fetch: vi.fn().mockRejectedValue(new Error("Preview unavailable")),
        } as unknown as Transport
      }
    >
      <LiveOutput
        threadId="one"
        gap={false}
        blocks={[
          {
            id: "run:run:input:1:0",
            kind: "user",
            text: "Reference",
            metadata,
          },
          {
            id: "run:run:input:1:1",
            kind: "media",
            text: "",
            value: { kind: "binary" },
            metadata,
          },
          {
            id: "run:run:input:2:0",
            kind: "user",
            text: "Reference again",
            metadata,
          },
        ]}
      />
    </TransportContext>,
  );
  expect(screen.getAllByText("example.png")).toHaveLength(2);
});
it("keeps saved raw output identity intact and does not load remote Markdown images or HTML", () => {
  const target = {
    producing_thread_id: "one",
    source_id: "C1",
    location: { kind: "root_text", message: 3, part: 0 },
  };
  const view = render(
    <SavedEntry
      entry={
        {
          position: 3,
          message_kind: "response",
          parts: [
            {
              kind: "assistant",
              text: "**Exact** source",
              comment_target: target,
            },
          ],
        } as Schema<"TranscriptEntry">
      }
    />,
  );
  expect(
    JSON.parse(
      view.container
        .querySelector("[data-saved-target]")!
        .getAttribute("data-saved-target")!,
    ),
  ).toEqual(target);
  expect(screen.getByText("**Exact** source")).toBeTruthy();
  view.rerender(
    <MessageText
      text={
        "![remote](https://example.com/tracking.png) <script>bad()</script>"
      }
    />,
  );
  expect(view.container.querySelector("img,script")).toBeNull();
});

it.each(["background_process", "async_subagent"])(
  "renders %s notices as system activity in saved and live output",
  (source) => {
    const text =
      "Background process process-example has exited. Call shell_wait for available output.";
    const parts = [
      { kind: "user", text, metadata: { "a13n.steering-source": source } },
      { kind: "system", text: "System context" },
      { kind: "user", text: "Request context", metadata: { display: false } },
    ];
    const view = render(
      <SavedEntry
        entry={
          {
            position: 0,
            message_kind: "request",
            parts,
          } as Schema<"TranscriptEntry">
        }
      />,
    );
    expect(screen.getByText("System notification")).toBeTruthy();
    expect(screen.queryByText("You")).toBeNull();
    expect(screen.queryByText("System context")).toBeNull();
    expect(screen.queryByText("Request context")).toBeNull();
    expect(screen.getByText(text).closest("details")).toBeTruthy();
    view.rerender(
      <LiveOutput
        gap={false}
        blocks={[
          { id: "notice", kind: "user", text, metadata: parts[0].metadata },
        ]}
      />,
    );
    expect(screen.getByText(text).closest("details")).toBeTruthy();
    expect(screen.queryByText("You")).toBeNull();
    view.rerender(
      <LiveOutput
        gap={false}
        blocks={[{ id: "real-user", kind: "user", text }]}
      />,
    );
    expect(screen.queryByText("System notification")).toBeNull();
    expect(screen.getByText(text).closest("details")).toBeNull();
  },
);
