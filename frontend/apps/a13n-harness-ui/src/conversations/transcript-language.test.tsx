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
import type { DisplayBlock } from "./stream";
import { LiveOutput, RecoveryNotice, SavedEntry } from "./transcript";
import { MessageText } from "./message-text";
import { LiveConnectionStatus } from "./live-connection";
import { ContextActivity } from "./context-activity";
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

it("translates saved transcript chrome while preserving model content, backend part kinds and the open disclosure", async () => {
  render(<SavedEntry entry={saved} threadId="one" />);
  const reasoning = screen.getByText("Thought").closest("details")!;
  await act(async () => {
    reasoning.open = true;
    reasoning.dispatchEvent(new Event("toggle"));
  });
  const reply = screen.getByText("Visible reply");
  const thought = screen.getByText("Private plan");
  expect(screen.getByText("for 3s")).toBeTruthy();
  expect(
    screen.getByText("Saved preview truncated by the server."),
  ).toBeTruthy();
  expect(screen.getByText("System updates · 2")).toBeTruthy();
  expect(screen.getByText(/Content omitted by the server\./)).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByText("思考").closest("details")).toBe(reasoning);
  expect(reasoning.open).toBe(true);
  expect(screen.getByText("Private plan")).toBe(thought);
  expect(screen.getByText("Visible reply")).toBe(reply);
  expect(screen.getByText("用时 3s")).toBeTruthy();
  expect(screen.getByText("服务端已截断已保存的预览。")).toBeTruthy();
  expect(screen.getByText("系统更新 · 2")).toBeTruthy();
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
    "Reconnecting to model… · 1 retry",
  );
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "recovery:one", state: "resumed", retries: 2 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "Model connection restored · 2 retries",
  );

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByRole("status").textContent).toBe(
    "模型连接已恢复 · 重试 2 次",
  );
  view.rerender(
    <RecoveryNotice
      recovery={{ id: "recovery:one", state: "retrying", retries: 1 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "正在重新连接模型… · 重试 1 次",
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

it("translates generated context phases and keeps their backend diagnostics verbatim", async () => {
  const view = render(
    <ContextActivity
      context="compaction"
      text=""
      status="In progress · model_rate_limited"
    />,
  );
  expect(screen.getByText("In progress · model_rate_limited")).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByText("进行中 · model_rate_limited")).toBeTruthy();
  view.rerender(
    <ContextActivity context="handoff" text="" status="Summary ready" />,
  );
  expect(screen.getByText("摘要已就绪")).toBeTruthy();
  // An unrecognized backend status stays verbatim.
  view.rerender(
    <ContextActivity context="handoff" text="" status="Queued upstream" />,
  );
  expect(screen.getByText("Queued upstream")).toBeTruthy();
});

it("translates the expanded image viewer used by diagrams and attachments", async () => {
  render(
    <ImagePreview
      src="data:image/png;base64,iVBORw0K"
      name="chart.png"
      close={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Actual size" })).toBeTruthy();
  expect(screen.getByLabelText("Download image")).toBeTruthy();

  await act(() => i18n.changeLanguage("zh-CN"));

  expect(screen.getByRole("button", { name: "实际大小" })).toBeTruthy();
  expect(screen.getByLabelText("下载图片")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "放大图片到实际大小" }),
  ).toBeTruthy();
  // The authored file name stays verbatim.
  expect(screen.getByText("chart.png")).toBeTruthy();
});
