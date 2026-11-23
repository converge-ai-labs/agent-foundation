import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { renderDiagram } from "a13n-ui/mermaid-render";
import { MarkdownContent } from "../../shared/markdown";
import "../../i18n";

vi.mock("a13n-ui/mermaid-render", () => ({ renderDiagram: vi.fn() }));
const source = 'flowchart LR\nA["开始"] --> B["完成"]';
const svg = "data:image/svg+xml,%3Csvg%2F%3E";
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  document.documentElement.classList.remove("dark");
});

it("renders both math syntaxes alongside Chinese Markdown without consuming code or remote images", () => {
  const text = [
    String.raw`**结果：**代入$x^2$得到\(y^2\)。`,
    "$$\n" + String.raw`\frac{1}{2}` + "\n$$",
    String.raw`\[\sum_{i=1}^n i\]`,
    "`$literal$` · ![remote image](https://example.com/tracker.png)",
    "<script>window.example = true</script>",
  ].join("\n\n");
  const view = render(<MarkdownContent agentReply text={text} />);
  expect(screen.getByText("结果：").tagName).toBe("STRONG");
  expect(view.container.querySelectorAll(".katex")).toHaveLength(4);
  expect(screen.getAllByRole("region", { name: "Math formula" })).toHaveLength(
    2,
  );
  expect(screen.getByText("$literal$").tagName).toBe("CODE");
  expect(screen.getByRole("link", { name: "remote image" })).toBeTruthy();
  expect(view.container.querySelector("img, script")).toBeNull();
  view.rerender(<MarkdownContent text={text} />);
  expect(view.container.querySelector(".katex")).toBeNull();
});

it("keeps incomplete streamed fences as code, then shows a copyable diagram once the fence closes", async () => {
  const user = userEvent.setup();
  vi.mocked(renderDiagram).mockResolvedValue(svg);
  const open = `\`\`\`\`mermaid\n${source}`;
  const view = render(<MarkdownContent agentReply text={open + "\n```"} />);
  expect(view.container.querySelector("pre")?.textContent).toBe(
    source + "\n```\n",
  );
  expect(renderDiagram).not.toHaveBeenCalled();
  view.rerender(
    <MarkdownContent agentReply text={open + "\n````\n\nAfter."} />,
  );
  await screen.findByRole("img", { name: "Mermaid diagram" });
  expect(renderDiagram).toHaveBeenCalledWith(source + "\n", false);
  expect(view.container.querySelector("pre")?.hidden).toBe(true);
  await user.click(screen.getByRole("button", { name: "Copy diagram source" }));
  expect(await navigator.clipboard.readText()).toBe(source + "\n");
  await user.click(screen.getByRole("button", { name: "Source" }));
  expect(view.container.querySelector("pre")?.hidden).toBe(false);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("After.")).toBeTruthy();
  document.documentElement.classList.add("dark");
  await waitFor(() =>
    expect(renderDiagram).toHaveBeenLastCalledWith(source + "\n", true),
  );
});

it("renders nested completed fences only in replies and leaves failed diagrams as copyable source", async () => {
  vi.mocked(renderDiagram).mockRejectedValue(new Error("Unsupported diagram"));
  const text = `> ~~~mermaid\n> ${source.replaceAll("\n", "\n> ")}\n> ~~~`;
  const view = render(<MarkdownContent text={text} />);
  expect(renderDiagram).not.toHaveBeenCalled();
  view.rerender(<MarkdownContent agentReply text={text} />);
  await screen.findByText(/Diagram preview unavailable/);
  expect(view.container.querySelector("blockquote pre")?.textContent).toBe(
    source + "\n",
  );
  expect(
    screen.getByRole("button", { name: "Copy diagram source" }),
  ).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
});
