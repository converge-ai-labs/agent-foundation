// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConversationTranscript } from "./transcript";
import type { Schema } from "../transport/client";
import type { DisplayBlock } from "./stream";

const time = "2026-11-12T01:00:00Z";
const publications: Schema<"BotMessage">[] = [
  {
    message_id: "user-human",
    input_id: "human",
    role: "user",
    text: "Check the result",
    created_at: time,
  },
  {
    message_id: "reply-final",
    role: "assistant",
    text: "Verified.",
    display_ids: ["native-final"],
    created_at: time,
  },
  {
    message_id: "proactive",
    role: "assistant",
    text: "Verified.",
    created_at: time,
  },
];
const marker = { display: false, harness_ui: { bot_input_id: "human" } };
const live: DisplayBlock[] = [
  {
    id: "marker",
    kind: "user",
    text: "Hidden return address",
    inputGroup: "input-group",
    metadata: marker,
  },
  {
    id: "human",
    kind: "user",
    text: "Check the result",
    inputGroup: "input-group",
    inputSource: "user",
    metadata: {
      source_id: "input-native-transport",
      harness_ui: { composer: { index: 0 } },
    },
  },
  { id: "thought", kind: "thinking", text: "Working reasoning" },
  {
    id: "tool",
    kind: "tool",
    name: "read_file",
    text: '{"path":"result.md"}',
    done: true,
    result: "Read result",
    toolCallId: "call-read",
  },
  { id: "native-final", kind: "assistant", text: "Verified." },
  {
    id: "hidden",
    kind: "assistant",
    text: "Private context",
    metadata: { display: false },
  },
  {
    id: "silent",
    kind: "tool",
    name: "finish_silently",
    text: "{}",
    done: true,
  },
];
const entries: Schema<"TranscriptEntry">[] = [
  {
    position: 0,
    message_kind: "request",
    timestamp: time,
    parts: [
      { kind: "user", text: "Hidden return address", metadata: marker },
      {
        kind: "user",
        text: "Check the result",
        metadata: {
          source_id: "input-native-transport",
          harness_ui: { composer: { index: 0 } },
        },
      },
    ],
  },
  {
    position: 1,
    message_kind: "response",
    timestamp: time,
    parts: [
      { kind: "thinking", text: "Working reasoning" },
      {
        kind: "tool_call",
        tool_name: "read_file",
        tool_call_id: "call-read",
        value: { path: "result.md" },
      },
      {
        kind: "tool_result",
        tool_name: "read_file",
        tool_call_id: "call-read",
        value: "Read result",
        outcome: "success",
      },
    ],
  },
  {
    position: 2,
    message_kind: "response",
    timestamp: time,
    parts: [
      { kind: "assistant", text: "Verified.", display_id: "native-final" },
      {
        kind: "assistant",
        text: "Private context",
        metadata: { display: false },
      },
      {
        kind: "tool_call",
        tool_name: "finish_silently",
        tool_call_id: "quiet",
        value: {},
      },
      {
        kind: "tool_result",
        tool_name: "finish_silently",
        tool_call_id: "quiet",
        value: null,
      },
    ],
  },
];
beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it.each([false, true])(
  "uses ordinary work folds and publication identity with saved=%s",
  (saved) => {
    const view = render(
      <ConversationTranscript
        threadId="thread_bot"
        entries={saved ? entries : []}
        blocks={saved ? [] : live}
        localInputs={[]}
        publications={publications}
        turns={
          saved
            ? [
                {
                  turn_id: "turn-human",
                  input_position: 0,
                  end_position: 3,
                  final_position: 2,
                  output_position: 2,
                  preview: "Check the result",
                },
              ]
            : []
        }
      />,
    );
    expect(screen.getAllByText("Check the result")).toHaveLength(1);
    // The native final and an independent equal-text proactive message stay distinct.
    expect(screen.getAllByText("Verified.")).toHaveLength(2);
    const work = screen.getByRole("button", { name: /Work log/ });
    fireEvent.click(work);
    expect(screen.getByText("Working reasoning")).toBeTruthy();
    expect(view.container.textContent).toContain("read_file");
    expect(view.container.textContent).not.toContain("Hidden return address");
    expect(view.container.textContent).not.toContain("Private context");
    expect(view.container.textContent).not.toContain("finish_silently");
  },
);
it("folds automatic work without a visible human input and retains prose after silent completion", () => {
  const view = render(
    <ConversationTranscript
      threadId="thread_bot"
      entries={[]}
      blocks={live.slice(2)}
      localInputs={[]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Work log/ }));
  expect(screen.getByText("Working reasoning")).toBeTruthy();
  expect(screen.getByText("Verified.")).toBeTruthy();
  expect(view.container.textContent).not.toContain("finish_silently");
});
it("keeps the native final at its own position before a later human steer", () => {
  const blocks: DisplayBlock[] = [
    {
      id: "native-final",
      kind: "assistant",
      text: "Verified.",
      timestamp: Date.parse(time),
    },
    {
      id: "later",
      kind: "user",
      text: "More context",
      inputGroup: "later",
      timestamp: Date.parse(time) + 1000,
    },
  ];
  const view = render(
    <ConversationTranscript
      threadId="thread_bot"
      entries={[]}
      blocks={blocks}
      localInputs={[]}
      publications={[publications[1]]}
    />,
  );
  expect(view.container.textContent!.indexOf("Verified.")).toBeLessThan(
    view.container.textContent!.indexOf("More context"),
  );
  expect(screen.getAllByText("Verified.")).toHaveLength(1);
});

it.each([false, true])(
  "bounds and orders fallback publications with real native ISO timestamps, saved=%s",
  (saved) => {
    const native: DisplayBlock[] = [
      {
        id: "middle",
        kind: "assistant",
        text: "Loaded work",
        timestamp: time,
      },
    ];
    const messages: Schema<"BotMessage">[] = [-1000, 0, 1000].map((offset) => ({
      message_id: `proactive-${offset}`,
      role: "assistant",
      text: `Publication ${offset}`,
      created_at: new Date(Date.parse(time) + offset).toISOString(),
    }));
    const view = render(
      <ConversationTranscript
        threadId="thread_bot"
        entries={
          saved
            ? [
                {
                  position: 0,
                  message_kind: "response",
                  timestamp: time,
                  parts: [{ kind: "assistant", text: "Loaded work" }],
                },
              ]
            : []
        }
        blocks={saved ? [] : native}
        localInputs={[]}
        publications={messages}
        hasEarlier
        hasLater
      />,
    );
    expect(screen.getByText("Publication 0")).toBeTruthy();
    expect(screen.queryByText("Publication -1000")).toBeNull();
    expect(screen.queryByText("Publication 1000")).toBeNull();
    view.rerender(
      <ConversationTranscript
        threadId="thread_bot"
        entries={
          saved
            ? [
                {
                  position: 0,
                  message_kind: "response",
                  timestamp: time,
                  parts: [{ kind: "assistant", text: "Loaded work" }],
                },
              ]
            : []
        }
        blocks={saved ? [] : native}
        localInputs={[]}
        publications={messages}
      />,
    );
    expect(screen.getByText("Publication -1000")).toBeTruthy();
    expect(screen.getByText("Publication 1000")).toBeTruthy();
    expect(
      view.container.textContent!.indexOf("Publication -1000"),
    ).toBeLessThan(view.container.textContent!.indexOf("Loaded work"));
    expect(view.container.textContent!.indexOf("Loaded work")).toBeLessThan(
      view.container.textContent!.indexOf("Publication 1000"),
    );
  },
);

it.each(["Sending…", "Not confirmed", "Not sent"])(
  "appends local %s after native work despite a two-second client clock skew",
  (status) => {
    const view = render(
      <ConversationTranscript
        threadId="thread_bot"
        entries={[]}
        blocks={[
          {
            id: "recent-work",
            kind: "assistant",
            text: "Recent server work",
            timestamp: Date.parse(time),
          },
        ]}
        localInputs={[]}
        hasEarlier
        publications={[
          {
            message_id: "user-local",
            role: "user",
            text: "Local steer",
            created_at: new Date(Date.parse(time) - 2000).toISOString(),
            status,
            local: true,
          },
        ]}
      />,
    );
    expect(screen.getByText(status)).toBeTruthy();
    expect(
      view.container.textContent!.indexOf("Recent server work"),
    ).toBeLessThan(view.container.textContent!.indexOf("Local steer"));
  },
);
it("keeps canonical interrupted inputs ordered by server time", () => {
  const view = render(
    <ConversationTranscript
      threadId="thread_bot"
      entries={[]}
      blocks={[
        {
          id: "later-work",
          kind: "assistant",
          text: "Later server work",
          timestamp: Date.parse(time),
        },
      ]}
      localInputs={[]}
      publications={[
        {
          message_id: "user-interrupted",
          role: "user",
          text: "Earlier admitted input",
          created_at: new Date(Date.parse(time) - 2000).toISOString(),
          status: "Didn't finish",
        },
      ]}
    />,
  );
  expect(
    view.container.textContent!.indexOf("Earlier admitted input"),
  ).toBeLessThan(view.container.textContent!.indexOf("Later server work"));
});
