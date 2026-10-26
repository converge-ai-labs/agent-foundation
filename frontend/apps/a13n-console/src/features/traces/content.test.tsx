import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { decodeContent, TraceContent, TraceJson } from "./content";
import { observationKind } from "./identity";
import { UNKNOWN } from "../../shared/unknown";
import { sourceChunks, TEXT_CHUNK_SIZE } from "./content-source";
import { mockIntersections } from "../../../tests/intersection-observer";
const intersect = mockIntersections();

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

it("loads large JSON while scrolling and copies the whole value", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  const value = { large: "a".repeat(110_000) };
  const text = JSON.stringify(value, null, 2);
  const { container } = render(<TraceJson value={value} />);
  expect(container.querySelectorAll("pre")).toHaveLength(1);
  expect(
    container.querySelector("pre")!.textContent.length,
  ).toBeLessThanOrEqual(TEXT_CHUNK_SIZE);
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  const chunks = [...container.querySelectorAll("[data-content-chunk]")];
  for (const chunk of chunks) intersect(chunk, true);
  expect(
    [...container.querySelectorAll("pre")]
      .map((node) => node.textContent)
      .join(""),
  ).toBe(text);
  intersect(chunks[0]!, false);
  expect(container.querySelectorAll("pre")).toHaveLength(chunks.length - 1);
  await user.click(screen.getByRole("button", { name: "Copy full text" }));
  expect(write).toHaveBeenCalledExactlyOnceWith(text);
});

it("mounts nearby messages, restores measured space on return, and retains full copy", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  const messages = Array.from({ length: 25 }, (_, index) => ({
    role: "user",
    content: `Message ${index}\n\n${"prose ".repeat(700)}\n\n\`\`\`json\n{"ok":true}\n\`\`\``,
  }));
  const source = JSON.stringify({ messages });
  const { container } = mount(source);
  expect(container.querySelectorAll("article")).toHaveLength(1);
  expect(screen.getByText("Message 0")).toBeTruthy();
  expect(screen.queryByText("Message 10")).toBeNull();
  expect(container.querySelector(".hljs")).toBeNull();
  const first = container.querySelector<HTMLElement>(
    '[data-content-chunk="0"]',
  )!;
  const later = container.querySelector<HTMLElement>(
    '[data-content-chunk="10"]',
  )!;
  vi.spyOn(first, "getBoundingClientRect").mockReturnValue({
    height: 1200,
  } as DOMRect);
  intersect(later, true);
  intersect(first, false);
  expect(screen.getByText("Message 10")).toBeTruthy();
  expect(screen.queryByText("Message 0")).toBeNull();
  expect(first.style.height).toBe("1200px");
  expect(container.querySelectorAll("article")).toHaveLength(1);
  intersect(first, true);
  intersect(later, false);
  expect(screen.getByText("Message 0")).toBeTruthy();
  expect(first.style.height).toBe("");
  await user.click(
    screen.getByRole("button", { name: "Copy retained content" }),
  );
  expect(write).toHaveBeenCalledExactlyOnceWith(
    JSON.stringify(source, null, 2),
  );
  await user.click(screen.getByRole("button", { name: "Raw JSON" }));
  expect(container.querySelectorAll("article")).toHaveLength(0);
  expect(container.querySelector("pre")!.textContent).toBe(
    sourceChunks(JSON.stringify(source, null, 2))[0],
  );
  await user.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.getByText("Message 0")).toBeTruthy();
});

it("retains nested source measurements when scrolling away from a long message", () => {
  const text = "x".repeat(TEXT_CHUNK_SIZE) + "Source tail";
  const { container } = mount(
    Array.from({ length: 12 }, (_, index) => ({
      role: "user",
      content: index === 0 ? text : `Message ${index}`,
    })),
  );
  const message = container.querySelector<HTMLElement>(
    '[data-content-chunk="0"]',
  )!;
  const tail = message.querySelector<HTMLElement>('[data-content-chunk="1"]')!;
  vi.spyOn(tail, "getBoundingClientRect").mockReturnValue({
    height: 600,
  } as DOMRect);
  intersect(tail, true);
  expect(screen.getByText("Source tail")).toBeTruthy();
  intersect(message, false);
  expect(screen.queryByText("Source tail")).toBeNull();
  intersect(message, true);
  const restored = message.querySelector<HTMLElement>(
    '[data-content-chunk="1"]',
  )!;
  expect(restored.style.height).toBe("600px");
  intersect(restored, true);
  expect(screen.getByText("Source tail")).toBeTruthy();
});

it("automatically loads nested blocks and tool calls without nested page controls", () => {
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
  expect(screen.queryByText("Part 11")).toBeNull();
  expect(screen.getByText("tool_0")).toBeTruthy();
  expect(screen.queryByText("tool_11")).toBeNull();
  for (const chunk of container.querySelectorAll('[data-content-chunk="11"]'))
    intersect(chunk, true);
  expect(screen.getByText("Part 11")).toBeTruthy();
  expect(screen.getByText("tool_11")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
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
