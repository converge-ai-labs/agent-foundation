// @vitest-environment node
import { describe, expect, it } from "vitest";
import { botConversation } from "./bot-conversation";
import type { Schema } from "./transport/client";
import {
  FocusDisplay,
  focusFrame,
  type DisplayBlock,
} from "./conversations/stream";

const time = "2026-11-12T01:00:00Z";
const live = (
  id: string,
  text: string,
  extra: Partial<DisplayBlock> = {},
): DisplayBlock => ({ id, kind: "assistant", text, timestamp: time, ...extra });
const saved = (
  item_id: string,
  text: string,
  final = false,
): Schema<"BotProse"> => ({ item_id, text, created_at: time, final });

describe("Bot conversation projection", () => {
  it("shows only root prose and retains the same row across replay and checkpoint", () => {
    const blocks = [
      live("intro", "I will check the saved result."),
      live("tool", "secret args", { kind: "tool" }),
      live("thought", "private", { kind: "thinking" }),
      live("child", "child output", { subagentRunId: "child-1" }),
      live("hidden", "host metadata", { metadata: { display: false } }),
      live("context", "hidden return address", { context: "handoff" }),
    ];
    expect(botConversation([], [], blocks).map((row) => row.text)).toEqual([
      "I will check the saved result.",
    ]);
    expect(
      botConversation([], [saved("intro", blocks[0].text)], blocks),
    ).toHaveLength(1);
    expect(
      botConversation([], [saved("intro", blocks[0].text)], []),
    ).toHaveLength(1);
  });
  it("replaces the native final with its publication by identity, without merging equal independent messages", () => {
    const messages: Schema<"BotMessage">[] = [
      {
        message_id: "reply-1",
        role: "assistant",
        text: "Verified.",
        created_at: time,
        display_ids: ["final"],
      },
      {
        message_id: "proactive-1",
        role: "assistant",
        text: "Verified.",
        created_at: time,
      },
    ];
    const result = botConversation(
      messages,
      [saved("intro", "Checking."), saved("final", "Verified.", true)],
      [live("final", "Verified.")],
    );
    expect(result.map((row) => row.message_id)).toEqual([
      "intro",
      "reply-1",
      "proactive-1",
    ]);
    expect(result.filter((row) => row.text === "Verified.")).toHaveLength(2);
  });
  it("joins the actual compact FocusDisplay item with saved prose and its final publication", () => {
    const display = new FocusDisplay();
    display.accept(
      focusFrame({
        kind: "snapshot",
        resume_cursor: null,
        snapshot: {
          epoch: "epoch",
          cutover_sequence: 0,
          thread: {
            thread: {
              thread_id: "thread_bot",
              root_activity: { state: "inactive" },
            },
            continuation_id: null,
          },
          children: { executions: [], total: 0 },
          recent_events: [],
          root_stream: {
            thread_id: "thread_bot",
            run_id: "run-bot",
            base_continuation_id: null,
            event_count: 1,
          },
        },
      }),
    );
    display.accept(
      focusFrame({
        kind: "root_stream",
        run_id: "run-bot",
        events: [
          {
            index: 0,
            event_type: "CUSTOM",
            payload_omitted: false,
            payload: {
              type: "CUSTOM",
              name: "a13n.display.snapshot",
              value: {
                items: [
                  {
                    id: "itm-native",
                    ordinal: 1,
                    kind: "text_message",
                    state: "completed",
                    first_stream_id: "1-1",
                    last_stream_id: "1-1",
                    started_at: time,
                    ended_at: time,
                    content: { role: "assistant", text: "Verified." },
                  },
                ],
                continuation: {
                  full_content: true,
                  position: { attempt: 1, sequence: 1 },
                },
              },
            },
          },
        ],
      }),
    );
    const blocks = [...display.blocks.values()];
    expect(blocks.map((block) => block.id)).toEqual(["itm-native"]);
    expect(
      botConversation([], [saved("itm-native", "Verified.", true)], blocks),
    ).toHaveLength(1);
    expect(
      botConversation(
        [
          {
            message_id: "publication",
            role: "assistant",
            text: "Verified.",
            created_at: time,
            display_ids: ["itm-native"],
          },
        ],
        [saved("itm-native", "Verified.", true)],
        blocks,
      ).map((row) => row.message_id),
    ).toEqual(["publication"]);
  });
  it("silent completion hides only the output tool and does not retract earlier prose", () => {
    expect(
      botConversation(
        [],
        [saved("intro", "Work is in progress.")],
        [live("quiet", "{}", { kind: "tool", name: "finish_silently" })],
      ).map((row) => row.text),
    ).toEqual(["Work is in progress."]);
  });
});
