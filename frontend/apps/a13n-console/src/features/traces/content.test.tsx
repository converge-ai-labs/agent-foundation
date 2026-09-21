import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { decodeContent, TraceContent, TraceJson } from "./content";
import { observationKind } from "./identity";
import { UNKNOWN } from "../../shared/unknown";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
function mount(value: unknown, media_type: string | null = null) {
  const cache = new QueryClient();
  return render(
    <QueryClientProvider client={cache}>
      <TraceContent content={{ media_type, value: value as never }} />
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
  expect(JSON.parse(raw ?? "")).toEqual({ media_type: null, value });
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

it("distinguishes unavailable, compact-omitted, empty text, and explicit JSON null", () => {
  const { rerender } = render(<TraceContent content={null} />);
  expect(screen.getByText(UNKNOWN)).toBeTruthy();
  rerender(<TraceContent content={null} compact />);
  expect(screen.getByText("Content omitted in Compact view.")).toBeTruthy();
  cleanup();
  mount(null, "application/json");
  expect(screen.getByText("null")).toBeTruthy();
  cleanup();
  mount("");
  expect(screen.getByText("Empty text")).toBeTruthy();
});

it("does not reinterpret scalar strings or malformed JSON and preserves large raw payloads", () => {
  for (const value of [
    "00123",
    "false",
    '{"incomplete":',
    "<context>retained</context>",
  ])
    expect(decodeContent(value)).toBe(value);
  expect(decodeContent('{"ok":true}')).toEqual({ ok: true });
  const value = { large: "a".repeat(110_000) };
  const { container } = render(<TraceJson value={value} />);
  expect(JSON.parse(container.querySelector("pre")?.textContent ?? "")).toEqual(
    value,
  );
  expect(container.querySelector(".hljs")).toBeNull();
});

it("distinguishes chat, tools, agents and phases without guessing unknown types", () => {
  const kind = (type: string, name = "operation") =>
    observationKind({ type, name });
  expect(kind("generation")).toBe("chat");
  expect(kind("TOOL")).toBe("tool");
  expect(kind("agent")).toBe("agent");
  expect(kind("span", "a13n.service.run_attempt")).toBe("agent");
  expect(kind("span", "harness.prepare")).toBe("phase");
  expect(kind("span", "a13n.service.persist")).toBe("data");
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
