// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { i18n } from "../i18n";
import type { Schema } from "../transport/client";
import { FocusDisplay, focusFrame, type DisplayBlock } from "./stream";
import {
  ConversationTranscript,
  LiveOutput,
  RecoveryNotice,
  SavedEntry,
} from "./transcript";
import { MessageText } from "./message-text";
import { LiveConnectionStatus } from "./live-connection";
import { ImagePreview } from "../shell/image-preview";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
);
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

const saved = {
  position: 0,
  message_kind: "response",
  parts: [
    { kind: "thinking", text: "Private plan", duration_ms: 3000 },
    { kind: "assistant", text: "Visible reply", text_truncated: true },
    { kind: "other", text: "Recorded step", value_omitted: true },
    {
      kind: "other",
      text: "First notice",
      metadata: { "a13n.steering-source": "background_process" },
    },
    {
      kind: "other",
      text: "Second notice",
      metadata: { "a13n.steering-source": "async_subagent" },
    },
  ],
} as Schema<"TranscriptEntry">;

// Replay live events through the reducer so the assertions cover the state it
// reports to the display layer, not hand-written blocks.
function replay(
  events: { event_type: string; payload: Record<string, unknown> }[],
) {
  const display = new FocusDisplay();
  display.accept(
    focusFrame({
      kind: "snapshot",
      resume_cursor: null,
      snapshot: {
        epoch: "epoch-one",
        cutover_sequence: 100,
        thread: {
          thread: {
            thread_id: "thread-one",
            created_at: "2026-09-12",
            updated_at: "2026-09-12",
            metadata_version: 1,
            archived: false,
            configuration: {
              version: 1,
              agent_source: { kind: "agent", id: "agent-one" },
              environment_profile_id: "local",
            },
            continuation_state: "initial",
            root_activity: { state: "inactive" },
          },
          continuation_id: null,
        },
        children: { executions: [], total: 0 },
        recent_events: [],
        root_stream: {
          thread_id: "thread-one",
          run_id: "run-one",
          base_continuation_id: null,
          event_count: events.length,
        },
      },
    }),
  );
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: events.map((event, index) => ({
        ...event,
        index,
        payload: { type: event.event_type, ...event.payload },
        payload_omitted: false,
      })),
    }),
  );
  display.accept(focusFrame({ kind: "ready", resume_cursor: "cursor-ready" }));
  return [...display.blocks.values()];
}

function contextEvent(payload: Record<string, unknown>) {
  return {
    event_type: "CUSTOM",
    payload: {
      name: "a13n.harness.context",
      value: { event: { payload: { ...payload } } },
    },
  };
}

it("translates saved transcript chrome while preserving model content, backend part kinds and the open disclosure", async () => {
  render(<SavedEntry entry={saved} threadId="one" />);
  const reasoning = screen.getByRole("button", { name: /Thought/ });
  fireEvent.click(reasoning);
  const reply = screen.getByText("Visible reply");
  const thought = screen.getByText("Private plan");
  expect(screen.getByText("for 3s")).toBeTruthy();
  expect(
    screen.getByText("Saved preview truncated by the server."),
  ).toBeTruthy();
  expect(screen.getByText("System updates - 2")).toBeTruthy();
  expect(screen.getByText(/Content omitted by the server\./)).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByRole("button", { name: /思考/ })).toBe(reasoning);
  expect(reasoning.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Private plan")).toBe(thought);
  expect(screen.getByText("Visible reply")).toBe(reply);
  expect(screen.getByText("用时 3s")).toBeTruthy();
  expect(screen.getByText("服务端已截断已保存的预览。")).toBeTruthy();
  expect(screen.getByText("系统更新 - 2")).toBeTruthy();
  expect(screen.getByText(/服务端已省略内容。/)).toBeTruthy();
  // Backend part kinds and notice text stay verbatim in both languages.
  expect(screen.getAllByText("other").length).toBeGreaterThan(0);
  expect(screen.getByText("First notice")).toBeTruthy();
});

it("translates live output chrome and the unnamed activity fallback without touching streamed text", async () => {
  const blocks: DisplayBlock[] = [
    { id: "run:reply", kind: "assistant", text: "Streaming reply" },
    { id: "run:step", kind: "activity", text: "Raw activity output" },
  ];
  render(<LiveOutput blocks={blocks} gap threadId="one" />);
  expect(screen.getByLabelText("Current unsaved output")).toBeTruthy();
  expect(screen.getByText("Activity")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain(
    "Some live updates are missing",
  );

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByLabelText("当前未保存的输出")).toBeTruthy();
  expect(screen.getByText("活动")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("部分实时更新缺失");
  expect(screen.getByText("Streaming reply")).toBeTruthy();
  expect(screen.getByText("Raw activity output")).toBeTruthy();
});

it("applies each language's plural rules to model reconnection counts", async () => {
  const view = render(
    <RecoveryNotice
      recovery={{ id: "recovery:one", state: "retrying", retries: 1 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "Reconnecting to model… - 1 retry",
  );
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "recovery:one", state: "resumed", retries: 2 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "Model connection restored - 2 retries",
  );

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByRole("status").textContent).toBe(
    "模型连接已恢复 - 重试 2 次",
  );
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "recovery:one", state: "retrying", retries: 1 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "正在重新连接模型… - 重试 1 次",
  );
});

it("translates known live-update states and keeps unknown transport states verbatim", async () => {
  vi.useFakeTimers();
  await i18n.changeLanguage("zh-CN");
  const view = render(
    <LiveConnectionStatus connection="Reconnecting" reconnections={2} />,
  );
  act(() => vi.advanceTimersByTime(1500));
  expect(screen.getByRole("status").textContent).toBe("正在重连实时更新…");
  fireEvent.click(screen.getByRole("button", { name: "实时更新连接" }));
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(screen.getByText("重连中")).toBeTruthy();
  expect(screen.getByText("重试 2 次")).toBeTruthy();
  view.rerender(
    <LiveConnectionStatus connection="Paused by operator" reconnections={2} />,
  );
  expect(screen.getByText("Paused by operator")).toBeTruthy();
});

it("translates markdown block controls and the image fallback around verbatim model content", async () => {
  render(
    <MessageText
      text={"```python\nprint(1)\n```\n\n| a |\n| - |\n| 1 |\n\n![](photo.png)"}
    />,
  );
  expect(screen.getByRole("region", { name: "python code" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Table" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy code" })).toBeTruthy();
  expect(screen.getByText("[Image: attachment]")).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByRole("region", { name: "python 代码" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "表格" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "复制代码" })).toBeTruthy();
  expect(screen.getByText("[图片：附件]")).toBeTruthy();
  // The language identifier and model-authored code remain verbatim.
  expect(screen.getByText("python")).toBeTruthy();
  expect(
    screen.getByRole("region", { name: "python 代码" }).textContent,
  ).toContain("print(1)");
});

it("translates live context states from the reducer and keeps their backend diagnostics verbatim", async () => {
  const started = replay([
    contextEvent({ type: "handoff_started", operation_id: "handoff-one" }),
  ]);
  const view = render(
    <LiveOutput blocks={started} gap={false} threadId="one" />,
  );
  expect(screen.getByText("Summary")).toBeTruthy();
  expect(screen.getByText("In progress")).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByText("摘要")).toBeTruthy();
  expect(screen.getByText("进行中")).toBeTruthy();
  const failed = replay([
    contextEvent({ type: "handoff_started", operation_id: "handoff-one" }),
    contextEvent({
      type: "handoff_failed",
      operation_id: "handoff-one",
      error_code: "model_rate_limited",
      failed_phase: "summarize",
      reason: "upstream quota exhausted",
    }),
  ]);
  view.rerender(<LiveOutput blocks={failed} gap={false} threadId="one" />);
  // The state is translated while the backend diagnostics stay verbatim.
  expect(
    screen.getByText(
      "失败 - model_rate_limited - summarize - upstream quota exhausted",
    ),
  ).toBeTruthy();
  const ready = replay([
    contextEvent({ type: "compaction_started", operation_id: "compact-one" }),
    {
      event_type: "CUSTOM",
      payload: {
        name: "a13n.context.compaction_summary",
        value: {
          event: { operation_id: "compact-one", summary: "Keep this context" },
        },
      },
    },
    contextEvent({ type: "compaction_completed", operation_id: "compact-one" }),
  ]);
  view.rerender(<LiveOutput blocks={ready} gap={false} threadId="one" />);
  expect(screen.getByText("压缩摘要")).toBeTruthy();
  expect(screen.getByText("已完成")).toBeTruthy();
  expect(screen.getAllByText("Keep this context").length).toBeGreaterThan(0);
});

it("translates generated execution outcomes and subagent states while preserving backend messages", async () => {
  const cancelled = replay([
    {
      event_type: "RUN_FINISHED",
      payload: {
        threadId: "thread-one",
        runId: "run-one",
        outcome: { type: "cancelled" },
      },
    },
  ]);
  const view = render(
    <LiveOutput blocks={cancelled} gap={false} threadId="one" />,
  );
  expect(screen.getByText("Execution cancelled")).toBeTruthy();
  expect(
    screen.getByText(
      "Run finished. Open its details to check follow-ups and environment results.",
    ),
  ).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByText("执行已取消")).toBeTruthy();
  expect(
    screen.getByText("运行已结束。打开详情查看后续事项与环境结果。"),
  ).toBeTruthy();
  const suspended = replay([
    {
      event_type: "RUN_FINISHED",
      payload: {
        threadId: "thread-one",
        runId: "run-one",
        outcome: {
          type: "interrupt",
          interrupts: [{ id: "interrupt-one", reason: "approval_required" }],
        },
        message: "Approve the pending command.",
      },
    },
  ]);
  view.rerender(<LiveOutput blocks={suspended} gap={false} threadId="one" />);
  expect(screen.getByText("执行已暂停")).toBeTruthy();
  // A backend message replaces the generated fallback and stays verbatim.
  expect(screen.getByText("Approve the pending command.")).toBeTruthy();
  const interruptWithoutMessage = replay([
    {
      event_type: "RUN_FINISHED",
      payload: {
        threadId: "thread-one",
        runId: "run-one",
        outcome: {
          type: "interrupt",
          interrupts: [{ id: "interrupt-one", reason: "approval_required" }],
        },
      },
    },
  ]);
  view.rerender(
    <LiveOutput blocks={interruptWithoutMessage} gap={false} threadId="one" />,
  );
  expect(screen.getByText("需要先回复，执行才能继续。")).toBeTruthy();
  const rawFallback = replay([
    {
      event_type: "RUN_FINISHED",
      payload: {
        threadId: "thread-one",
        runId: "run-one",
        outcome: { type: "cancelled" },
        message:
          "Run finished. Open its details to check follow-ups and environment results.",
      },
    },
  ]);
  view.rerender(<LiveOutput blocks={rawFallback} gap={false} threadId="one" />);
  expect(
    screen.getByText(
      "Run finished. Open its details to check follow-ups and environment results.",
    ),
  ).toBeTruthy();
  const child: DisplayBlock[] = [
    {
      id: "run-one:inline:child-one",
      kind: "activity",
      childName: "Media understanding",
      childState: "running",
      text: "",
    },
  ];
  view.rerender(<LiveOutput blocks={child} gap={false} threadId="one" />);
  // The subagent label is composed at display time around its backend name.
  expect(screen.getByText("子智能体 - Media understanding")).toBeTruthy();
  expect(screen.getByText("运行中")).toBeTruthy();
});

it("translates the expanded image viewer while keeping its open and zoom state", async () => {
  render(
    <ImagePreview
      src="data:image/png;base64,iVBORw0K"
      name="chart.png"
      close={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Actual size" })).toBeTruthy();
  expect(screen.getByLabelText("Download image")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Actual size" }));
  const image = screen.getByRole("img", { name: "chart.png" });
  const canvas = image.closest("[data-actual-size]")!;
  expect(canvas.getAttribute("data-actual-size")).toBe("true");
  expect(screen.getByRole("button", { name: "Fit to screen" })).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  // The viewer stays open and zoomed, and keeps the same image element.
  expect(screen.getByRole("img", { name: "chart.png" })).toBe(image);
  expect(image.getAttribute("src")).toBe("data:image/png;base64,iVBORw0K");
  expect(canvas.getAttribute("data-actual-size")).toBe("true");
  expect(screen.getByRole("button", { name: "适应屏幕" })).toBeTruthy();
  expect(screen.getByLabelText("下载图片")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "将图片缩放至适应屏幕" }),
  ).toBeTruthy();
  // The authored file name stays verbatim.
  expect(screen.getByText("chart.png")).toBeTruthy();
});

it("translates an incomplete saved execution preview without changing its open reader or payload", async () => {
  await i18n.changeLanguage("en");
  const entry = {
    position: 0,
    message_kind: "response",
    parts: [
      {
        kind: "tool_call",
        tool_name: "view",
        tool_call_id: "read-one",
        value: { file_path: "/tmp/Read" },
      },
    ],
  } as Schema<"TranscriptEntry">;
  render(
    <ConversationTranscript
      entries={[entry]}
      turns={[
        { turn_id: "one", input_position: 0, end_position: 1, preview: "" },
      ]}
      blocks={[]}
      localInputs={[]}
      threadId="one"
    />,
  );
  const heading = screen.getByRole("button", {
    name: /Read 1 file.*Work log/,
  });
  fireEvent.click(heading);
  expect(heading.getAttribute("aria-expanded")).toBe("true");
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(
    screen.getByRole("button", {
      name: /读取 1 个文件.*工作日志/,
    }),
  ).toBe(heading);
  expect(heading.getAttribute("aria-expanded")).toBe("true");
  expect(heading.textContent).not.toContain("/tmp/Read");
});
