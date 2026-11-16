// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { LanguageChoice, LanguageProvider } from "../i18n/language";
import { i18n } from "../i18n";
import { ToolActivity, ToolCall } from "./tool-call";
import type { ToolView } from "./tool-presentation";

beforeEach(() => {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);
});
afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  await i18n.changeLanguage("en");
});

async function language(name: string) {
  await userEvent.click(screen.getByRole("combobox"));
  await userEvent.click(await screen.findByRole("option", { name }));
}

function show(children: React.ReactNode) {
  render(
    <LanguageProvider>
      <LanguageChoice />
      <MemoryRouter>{children}</MemoryRouter>
    </LanguageProvider>,
  );
}

const completed = (
  id: string,
  name: string,
  input: unknown,
  result: unknown,
): ToolView => ({
  id,
  name,
  input,
  result,
  inputComplete: true,
  stopped: true,
});

it("translates a tool call and its details while keeping paths and recorded content", async () => {
  show(
    <ToolCall
      tool={completed(
        "read",
        "view",
        { file_path: "src/app.tsx" },
        {
          content: "export default null;\n",
        },
      )}
    />,
  );
  const disclosure = screen.getByRole("button", { name: "Read src/app.tsx" });
  fireEvent.click(disclosure);
  expect(screen.getByLabelText("Read content")).toBeTruthy();
  expect(screen.getByText("Input & result")).toBeTruthy();
  await language("简体中文");
  expect(screen.getByRole("button", { name: "读取 src/app.tsx" })).toBe(
    disclosure,
  );
  expect(disclosure.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("src/app.tsx")).toBeTruthy();
  const content = screen.getByLabelText("读取的内容");
  expect(content.textContent).toContain("export default null;");
  expect(screen.getByRole("button", { name: "复制读取的内容" })).toBeTruthy();
  expect(screen.getByText("输入与结果")).toBeTruthy();
});

it("keeps an unknown tool name and an unknown process phase verbatim", async () => {
  show(
    <>
      <ToolCall
        tool={{
          id: "mcp",
          name: "notion__search_pages",
          input: { query: "roadmap" },
          inputComplete: true,
        }}
      />
      <ToolCall
        tool={completed(
          "process",
          "shell_info",
          { process_id: "proc-1" },
          {
            status: { phase: "paused" },
          },
        )}
      />
    </>,
  );
  expect(screen.queryByText("Awaiting result")).toBeNull();
  expect(screen.getByText("Paused at last check")).toBeTruthy();
  await language("简体中文");
  expect(screen.getByText("notion__search_pages")).toBeTruthy();
  expect(screen.getByText("Paused at last check")).toBeTruthy();
  expect(screen.queryByText("等待结果")).toBeNull();
  expect(screen.getByText("查看进程")).toBeTruthy();
});

it("translates grouped exploration titles and the operations they contain", async () => {
  show(
    <ToolActivity
      tools={[
        completed("one", "view", { file_path: "src/app.tsx" }, { content: "" }),
        completed(
          "two",
          "view",
          { file_path: "src/main.tsx" },
          { content: "" },
        ),
        completed("three", "grep", { pattern: "render" }, { ok: true }),
      ]}
    />,
  );
  const group = screen.getByRole("button", {
    name: "Explored 2 files - 1 lookup",
  });
  fireEvent.click(group);
  await language("简体中文");
  expect(
    screen.getByRole("button", { name: "已查看 2 个文件 - 1 次查找" }),
  ).toBe(group);
  expect(group.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByText("读取").length).toBe(2);
  expect(screen.getByText("搜索")).toBeTruthy();
  expect(screen.getByText("src/main.tsx")).toBeTruthy();
  expect(screen.getByText("render")).toBeTruthy();
});

it("translates execution counts in the group title and its observed phase", async () => {
  show(
    <ToolActivity
      tools={[completed("listed", "tool_execution_info", {}, [{}, {}, {}])]}
    />,
  );
  expect(screen.getByText("Tool calls - 1 operation")).toBeTruthy();
  expect(screen.getByText("3 executions listed")).toBeTruthy();
  await language("简体中文");
  expect(screen.getByText("工具调用 - 1 次操作")).toBeTruthy();
  expect(screen.getByText("列出 3 次执行")).toBeTruthy();
});

it("translates question status without rewriting the recorded question or answer", async () => {
  const questions = [
    {
      header: "Scope",
      question: "Which part should I localize?",
      options: [{ label: "Tool details", description: "The expanded panel" }],
    },
  ];
  show(
    <ToolCall
      tool={{
        id: "question",
        name: "ask_user_question",
        input: { questions },
        inputComplete: true,
      }}
    />,
  );
  expect(screen.getByLabelText("Question")).toBeTruthy();
  expect(screen.getByText("Awaiting response")).toBeTruthy();
  await language("简体中文");
  expect(screen.getByLabelText("问题")).toBeTruthy();
  expect(screen.getByText("等待回答")).toBeTruthy();
  expect(screen.getByText("Scope")).toBeTruthy();
  expect(screen.getByText("Which part should I localize?")).toBeTruthy();
  expect(screen.getByText("问题与详情")).toBeTruthy();
});

it("translates an answered question while keeping the submitted answer", async () => {
  show(
    <ToolCall
      tool={completed(
        "answered",
        "ask_user_question",
        {
          questions: [
            { header: "Scope", question: "Which part should I localize?" },
          ],
        },
        {
          answers: { "Which part should I localize?": ["Tool details"] },
          response: "Start with the expanded panel.",
        },
      )}
    />,
  );
  expect(screen.getByLabelText("Answers")).toBeTruthy();
  expect(screen.getByText("Answered")).toBeTruthy();
  expect(screen.getByText("Response")).toBeTruthy();
  await language("简体中文");
  expect(screen.getByLabelText("回答")).toBeTruthy();
  expect(screen.getByText("已回答")).toBeTruthy();
  expect(screen.getByText("响应")).toBeTruthy();
  expect(screen.getByText("Scope")).toBeTruthy();
  expect(screen.getByText("Tool details")).toBeTruthy();
  expect(screen.getByText("Start with the expanded panel.")).toBeTruthy();
});

it("preserves a raw tool name that collides with a translated label and localizes the empty fallback", async () => {
  show(
    <>
      <ToolCall
        tool={{ id: "mcp-search", name: "Search", inputComplete: true }}
      />
      <ToolCall tool={{ id: "unnamed", name: "", inputComplete: true }} />
    </>,
  );
  await language("简体中文");
  expect(screen.getByText("Search")).toBeTruthy();
  expect(screen.getByText("工具")).toBeTruthy();
});

it("presents dynamic creation with a translated action and the child's authored name", async () => {
  show(
    <ToolCall
      tool={completed(
        "spawn",
        "spawn_subagent",
        { name: "Checkout reviewer", prompt: "Check retries" },
        { execution_id: "exec-child" },
      )}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Start subagent Checkout reviewer" }),
  ).toBeTruthy();
  await language("简体中文");
  expect(
    screen.getByRole("button", { name: "启动子智能体 Checkout reviewer" }),
  ).toBeTruthy();
});
