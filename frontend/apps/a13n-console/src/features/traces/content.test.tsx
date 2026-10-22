import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { decodeContent, TraceContent, TraceJson } from "./content";
import { observationKind } from "./identity";
import { UNKNOWN } from "../../shared/unknown";
import { PagedSource, TEXT_PAGE_SIZE } from "./content-pages";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function mount(value: unknown) {
  const cache = new QueryClient();
  return render(
    <QueryClientProvider client={cache}>
      <TraceContent content={value} />
    </QueryClientProvider>,
  );
}

it("extracts retained OTel messages, tool arguments and results without losing raw fields", async () => {
  const user = userEvent.setup();
  const value = JSON.stringify({
    messages: [
      {
        role: "system",
        parts: [{ type: "text", content: "Follow **instructions**." }],
      },
      {
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            name: "view",
            id: "call-1",
            arguments: '{"file_path":"release.txt"}',
          },
        ],
      },
      {
        role: "user",
        parts: [
          {
            type: "tool_call_response",
            name: "view",
            id: "call-1",
            result: { ok: true, content: "## Release\nReady to ship." },
          },
        ],
      },
    ],
    tools: [{ name: "view", parameters: { type: "object" } }],
    vendor_extension: { retained: 7 },
  });
  const { container } = mount(value);
  expect(screen.getByText("System")).toBeTruthy();
  expect(screen.getByText("instructions").tagName).toBe("STRONG");
  expect(screen.getAllByText("view")).toHaveLength(2);
  expect(container.querySelector(".hljs-attr")?.textContent).toBe(
    '"file_path"',
  );
  expect(screen.getByRole("heading", { name: "Release" })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Tool definitions/ })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Raw JSON" }));
  const raw = container.querySelector("pre code")?.textContent;
  expect(JSON.parse(raw ?? "")).toEqual(value);
  expect(
    screen.getByRole("button", { name: "Copy retained content" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.getByRole("heading", { name: "Release" })).toBeTruthy();
});

it("renders root content blocks, Chat Completions calls, and preserves unknown media", () => {
  const { container } = mount([
    {
      role: "user",
      content: [
        { type: "text", text: "Hello" },
        {
          type: "image_url",
          image_url: { url: "https://example.com/private.png" },
        },
      ],
    },
    {
      role: "assistant",
      content: null,
      tool_calls: [
        {
          id: "call-2",
          type: "function",
          function: { name: "search", arguments: '{"query":"release"}' },
        },
      ],
    },
  ]);
  expect(screen.getByText("Hello")).toBeTruthy();
  expect(screen.getByText("search")).toBeTruthy();
  expect(container.textContent).toContain("https://example.com/private.png");
  expect(container.querySelector("img")).toBeNull();
});

it("keeps JSON fences inert, highlights tokens, and does not execute HTML", () => {
  const { container } = render(
    <TraceJson
      value={{
        text: "```\n<script>alert(1)</script>\n```",
        count: 3,
        ok: false,
      }}
    />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector(".hljs-number")?.textContent).toBe("3");
  expect(
    JSON.parse(container.querySelector("code")?.textContent ?? "").text,
  ).toContain("<script>");
});

it("distinguishes unavailable content from empty text", () => {
  render(<TraceContent content={null} />);
  expect(screen.getByText(UNKNOWN)).toBeTruthy();
  cleanup();
  mount("");
  expect(screen.getByText("Empty text")).toBeTruthy();
});

it("does not reinterpret scalar strings or malformed JSON", () => {
  for (const value of [
    "00123",
    "false",
    '{"incomplete":',
    "<context>retained</context>",
  ])
    expect(decodeContent(value)).toBe(value);
  expect(decodeContent('{"ok":true}')).toEqual({ ok: true });
});

it("pages large JSON without losing source and copies the whole value", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  const value = { large: "a".repeat(110_000) };
  const { container } = render(<TraceJson value={value} />);
  const chunks: string[] = [];
  const next = screen.getByRole("button", { name: "Next" });
  while (true) {
    const chunk = container.querySelector("pre")!.textContent;
    expect(chunk.length).toBeLessThanOrEqual(TEXT_PAGE_SIZE);
    chunks.push(chunk);
    if ((next as HTMLButtonElement).disabled) break;
    await user.click(next);
  }
  expect(JSON.parse(chunks.join(""))).toEqual(value);
  expect(container.querySelector(".hljs")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Copy full text" }));
  expect(write).toHaveBeenCalledExactlyOnceWith(JSON.stringify(value, null, 2));
});

it("pages long text without splitting emoji or retaining another source's page", async () => {
  const user = userEvent.setup();
  const text = "a".repeat(TEXT_PAGE_SIZE - 1) + "😀tail";
  const view = render(<PagedSource text={text} />);
  expect(view.container.querySelector("pre")!.textContent).toBe(
    "a".repeat(TEXT_PAGE_SIZE - 1),
  );
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(view.container.querySelector("pre")!.textContent).toBe("😀tail");
  view.rerender(<PagedSource text={"b".repeat(TEXT_PAGE_SIZE + 1)} />);
  expect(view.container.querySelector("pre")!.textContent).toBe(
    "b".repeat(TEXT_PAGE_SIZE),
  );
  view.rerender(<PagedSource text={text} />);
  expect(
    (screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("mounts one message page, bounds aggregate highlighting, and retains complete raw copy", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  const messages = Array.from({ length: 25 }, (_, index) => ({
    role: "user",
    content: `Message ${index}\n\n${"prose ".repeat(700)}\n\n\`\`\`json\n{"ok":true}\n\`\`\``,
  }));
  const source = JSON.stringify({ messages });
  const { container } = mount(source);
  expect(container.querySelectorAll("article")).toHaveLength(10);
  expect(screen.getByText("Message 0")).toBeTruthy();
  expect(screen.queryByText("Message 10")).toBeNull();
  expect(container.querySelector(".hljs")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByText("Message 10")).toBeTruthy();
  expect(screen.queryByText("Message 0")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(container.querySelectorAll("article")).toHaveLength(5);
  expect(screen.getByText("Message 24")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Previous" }));
  expect(screen.getByText("Message 10")).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Copy retained content" }),
  );
  expect(write).toHaveBeenCalledExactlyOnceWith(
    JSON.stringify(source, null, 2),
  );
  await user.click(screen.getByRole("button", { name: "Raw JSON" }));
  expect(container.querySelectorAll("article")).toHaveLength(0);
  expect(container.querySelector("pre")!.textContent).toBe(
    JSON.stringify(source, null, 2).slice(0, TEXT_PAGE_SIZE),
  );
  await user.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.getByText("Message 0")).toBeTruthy();
});

it("bounds content blocks and Chat Completions tool calls inside a single message", async () => {
  const user = userEvent.setup();
  const { container } = mount([
    {
      role: "assistant",
      content: Array.from({ length: 12 }, (_, i) => ({
        type: "text",
        text: `Part ${i}`,
      })),
      tool_calls: Array.from({ length: 12 }, (_, i) => ({
        type: "function",
        function: { name: `tool_${i}`, arguments: "{}" },
      })),
    },
  ]);
  expect(screen.getByText("Part 0")).toBeTruthy();
  expect(screen.queryByText("Part 10")).toBeNull();
  expect(screen.getByText("tool_0")).toBeTruthy();
  expect(screen.queryByText("tool_10")).toBeNull();
  await user.click(screen.getAllByRole("button", { name: "Next" })[0]!);
  expect(screen.getByText("Part 11")).toBeTruthy();
  await user.click(screen.getAllByRole("button", { name: "Next" })[1]!);
  expect(screen.getByText("tool_11")).toBeTruthy();
  expect(container.querySelectorAll("article")).toHaveLength(1);
});

it("distinguishes chat, tools, agents and phases without guessing unknown types", () => {
  const kind = (type: string, name = "operation") =>
    observationKind({ kind: type, name });
  expect(kind("generation")).toBe("chat");
  expect(kind("TOOL")).toBe("tool");
  expect(kind("agent")).toBe("agent");
  expect(kind("span", "harness.run")).toBe("agent");
  expect(kind("span", "harness.prepare")).toBe("phase");
  expect(kind("future-kind", "harness.prepare")).toBe("span");
});

it("makes producer context readable without hiding markup or rounding large integers", () => {
  const source =
    '<agent-context source="harness">\n{"run_id":"run-1"}\n</agent-context>';
  const { container } = mount({
    content: [
      { type: "text", text: source },
      {
        type: "text",
        text: 'Environment context:\n{"mounts":[],"ready":true}',
      },
    ],
  });
  expect(container.querySelector("code.language-xml")?.textContent).toContain(
    source,
  );
  expect(
    container.querySelector(".language-json .hljs-attr")?.textContent,
  ).toBe('"mounts"');
  for (const exact of ['{"tokens":9007199254740993}', '{"value":1e999}']) {
    expect(decodeContent(exact)).toBe(exact);
  }
});

it("retains unfamiliar role names and untrusted literal markup safely", () => {
  const { container } = mount([
    { role: "constructor", content: "<script>alert(1)</script>" },
    {
      role: "future-role",
      parts: [{ type: "future-type", data: { value: 2 } }],
    },
  ]);
  expect(screen.getByText("constructor")).toBeTruthy();
  expect(screen.getByText("future-role")).toBeTruthy();
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
  expect(container.textContent).toContain("future-type");
});
