// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Schema, Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { SavedEntry, LiveOutput, MessageText } from "./transcript";

afterEach(cleanup);
it("keeps live prose visible when a stream gap needs a warning", () => {
  const blocks = [{ id: "reply", kind: "assistant" as const, text: "Done." }];
  const view = render(<LiveOutput blocks={blocks} gap={false} />);
  expect(screen.getByText("Done.")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(<LiveOutput blocks={blocks} gap />);
  expect(screen.getByRole("status").textContent).toContain(
    "Some live content is unavailable",
  );
});
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
it("renders saved output and does not load remote Markdown images or HTML", () => {
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
            },
          ],
        } as Schema<"TranscriptEntry">
      }
    />,
  );
  expect(screen.getByText("Exact").tagName).toBe("STRONG");
  expect(screen.queryByRole("button", { name: /comment/i })).toBeNull();
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
    const title =
      source === "background_process" ? "Process update" : "Subagent update";
    expect(screen.getByText(title).closest("details")?.open).toBe(false);
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
    expect(screen.queryByText(title)).toBeNull();
    expect(screen.getByText(text).closest("details")).toBeNull();
  },
);

it("omits model-only tool attachments without hiding genuine user media", () => {
  const text = JSON.stringify({
    kind: "binary",
    media_type: "image/png",
    size_bytes: 12,
    payload_omitted: true,
  });
  const part = {
    kind: "media",
    text,
    metadata: { media: true, display: false },
  };
  const view = render(
    <SavedEntry
      entry={
        {
          position: 0,
          message_kind: "request",
          parts: [part],
        } as Schema<"TranscriptEntry">
      }
    />,
  );
  expect(view.container.textContent).toBe("");
  view.rerender(
    <LiveOutput
      gap={false}
      blocks={[{ ...part, kind: "media", id: "tool-image" }]}
    />,
  );
  expect(view.container.textContent).toBe("");
  view.rerender(
    <SavedEntry
      entry={
        {
          position: 0,
          message_kind: "request",
          parts: [{ ...part, metadata: { media: true, display: true } }],
        } as Schema<"TranscriptEntry">
      }
    />,
  );
  expect(screen.getByText("User")).toBeTruthy();
  expect(view.container.textContent).toContain("image/png");
  expect(view.container.textContent).not.toContain("payload_omitted");
});

it("renders retained root errors alongside historical output without making them assistant prose", () => {
  render(
    <SavedEntry
      entry={{
        position: 0,
        message_kind: "response",
        parts: [{ kind: "assistant", text: "Partial answer" }],
        failures: [
          {
            id: "failure-old",
            run_id: "run-old",
            message: "Earlier provider failure",
          },
        ],
      }}
    />,
  );
  expect(screen.getByText("Partial answer")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain(
    "Earlier provider failure",
  );
});
