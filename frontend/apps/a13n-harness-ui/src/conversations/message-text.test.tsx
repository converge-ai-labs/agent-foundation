// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MessageText } from "./message-text";
import { GuestAccessContext } from "../transport/context";
import { renderDiagram } from "./mermaid-render";
import { closedFence } from "./markdown-block";
import { OpenHostFile } from "./tool-call";
const highlights = vi.hoisted(() => vi.fn());
vi.mock("lowlight", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lowlight")>();
  return {
    ...actual,
    createLowlight: (...args: Parameters<typeof actual.createLowlight>) => {
      const highlighter = actual.createLowlight(...args);
      const highlight = highlighter.highlight;
      highlighter.highlight = (...args) => {
        highlights(...args);
        return highlight(...args);
      };
      return highlighter;
    },
  };
});
vi.mock("./mermaid-render", () => ({ renderDiagram: vi.fn() }));
// Preview HTTP and decoding have their own integration tests; these cases own
// Markdown rendering and workbench navigation independently of native access.
vi.mock("./linked-media-preview", () => ({ LinkedMediaPreview: () => null }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  document.documentElement.classList.remove("dark");
});

it("renders inline and display math with accessible source and scrollable display regions", () => {
  const view = render(
    <MessageText
      text={String.raw`Inline $E = mc^2$.

$$
\frac{1}{2} + \sqrt{x}
$$

~~~math
\sum_{i=1}^{n} i
~~~
`}
    />,
  );
  expect(view.container.querySelectorAll(".katex")).toHaveLength(3);
  expect(view.container.querySelectorAll("math")).toHaveLength(3);
  expect(view.container.querySelector("annotation")?.textContent).toBe(
    "E = mc^2",
  );
  expect(screen.getAllByRole("region", { name: "Math formula" })).toHaveLength(
    2,
  );
  for (const region of screen.getAllByRole("region", { name: "Math formula" }))
    expect(region.tabIndex).toBe(0);
});

it("keeps escaped dollars, inline code and ordinary code fences literal", () => {
  const source = "\\$5 and \\$10; `$x$`\n\n```latex\n$E = mc^2$\n```";
  const view = render(<MessageText text={source} />);
  expect(view.container.querySelector(".katex")).toBeNull();
  expect(view.container.textContent).toContain("$5 and $10; $x$");
  expect(view.container.querySelector("pre code")?.textContent).toBe(
    "$E = mc^2$\n",
  );
});

it("recovers from incomplete streamed math and keeps malformed formulas readable", () => {
  const view = render(<MessageText text={"Before $\\frac{1}"} />);
  expect(view.container.textContent).toContain("Before");
  view.rerender(<MessageText text={"Before $\\frac{1}{2}$ after"} />);
  expect(view.container.querySelector(".katex")).not.toBeNull();
  view.rerender(<MessageText text={"$\\frac{$\n\nStill readable"} />);
  expect(view.container.querySelector(".katex-error")?.textContent).toBe(
    "\\frac{",
  );
  expect(screen.getByText("Still readable")).toBeTruthy();
});

it("does not allow math to create links, remote images or trusted HTML", () => {
  const view = render(
    <MessageText
      text={String.raw`$\href{https://example.com}{link}$

$\includegraphics{https://example.com/image.png}$

$\htmlClass{injected}{x}$

<script>alert(1)</script>`}
    />,
  );
  expect(view.container.querySelector("a, img, script, .injected")).toBeNull();
});

it("opens same-instance Host file links in the workbench and leaves external links separate", () => {
  const open = vi.fn();
  render(
    <OpenHostFile value={open}>
      <MessageText
        text={
          "[Open report](/threads/thread-a?native=files&native_path=%2Ftmp%2Freport.md) [Website](https://example.com/?native=files&native_path=%2Ftmp%2Fother.md)"
        }
      />
    </OpenHostFile>,
  );
  const report = screen.getByRole("link", { name: "Open report" });
  expect(report.getAttribute("target")).toBeNull();
  fireEvent.click(report);
  expect(open).toHaveBeenCalledWith("/tmp/report.md");
  const website = screen.getByRole("link", { name: "Website" });
  expect(website.getAttribute("target")).toBe("_blank");
});

it("keeps GFM alignment and wraps wide tables in a keyboard-scrollable region", () => {
  const source = "| Name | Count |\n| :--- | ---: |\n| Ready | 123 |";
  render(<MessageText text={source} />);
  const region = screen.getByRole("region", { name: "Table" });
  expect(region.tabIndex).toBe(0);
  expect(screen.getByRole("cell", { name: "123" }).style.textAlign).toBe(
    "right",
  );
});

it("copies original code including newlines", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  render(<MessageText text={'```ts\nconst value = "test";\n```'} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
  await screen.findByText("Copied");
  expect(writeText).toHaveBeenCalledWith('const value = "test";\n');
});

it("waits for a closing fence and does not rerender a complete diagram for later streamed prose", async () => {
  vi.mocked(renderDiagram).mockResolvedValue("data:image/svg+xml,diagram");
  const start = "```mermaid\nflowchart LR\nA --> B\n";
  const view = render(<MessageText text={start} />);
  expect(renderDiagram).not.toHaveBeenCalled();
  expect(screen.getByText("Waiting for the complete diagram…")).toBeTruthy();
  view.rerender(<MessageText text={`${start}\`\`\`\n`} />);
  const image = await screen.findByAltText("Mermaid diagram");
  expect(renderDiagram).toHaveBeenCalledTimes(1);
  view.rerender(<MessageText text={`${start}\`\`\`\n\nMore output`} />);
  expect(screen.getByAltText("Mermaid diagram")).toBe(image);
  expect(renderDiagram).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Source" }));
  expect(screen.getByText(/flowchart LR/)).toBeTruthy();
});

it("retains a diagram during theme replacement and ignores stale asynchronous results", async () => {
  let resolve!: (value: string) => void;
  vi.mocked(renderDiagram)
    .mockResolvedValueOnce("data:image/svg+xml,light")
    .mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
  render(<MessageText text={"```mermaid\nflowchart LR\nA --> B\n```"} />);
  const image = (await screen.findByAltText(
    "Mermaid diagram",
  )) as HTMLImageElement;
  act(() => document.documentElement.classList.add("dark"));
  await waitFor(() => expect(renderDiagram).toHaveBeenCalledTimes(2));
  expect(image.src).toBe("data:image/svg+xml,light");
  await act(async () => resolve("data:image/svg+xml,dark"));
  expect(image.src).toBe("data:image/svg+xml,dark");
});

it("falls back to exact source for invalid diagrams without hiding the rest of the message", async () => {
  vi.mocked(renderDiagram).mockRejectedValue(new Error("Invalid syntax"));
  render(
    <MessageText text={"```mermaid\ninvalid syntax\n```\n\nStill readable"} />,
  );
  await screen.findByText(/Diagram preview unavailable/);
  expect(screen.getByText("invalid syntax")).toBeTruthy();
  expect(screen.getByText("Still readable")).toBeTruthy();
});

it("matches fence type and length instead of mistaking partial or nested backticks for completion", () => {
  expect(closedFence("```mermaid\nflowchart LR\n```")).toBe(true);
  expect(closedFence("~~~~mermaid\nflowchart LR\n~~~~")).toBe(true);
  expect(closedFence("````mermaid\nflowchart LR\n```")).toBe(false);
  expect(closedFence("```mermaid\nflowchart LR\n~~~")).toBe(false);
});

it("highlights declared code without changing its source bytes and leaves unknown languages as text", () => {
  const source = 'const label = "<script>alert(1)</script>";\n';
  const view = render(
    <MessageText text={`\`\`\`typescript\n${source}\`\`\``} />,
  );
  const code = view.container.querySelector("pre code")!;
  expect(code.textContent).toBe(source);
  expect(code.querySelector(".hljs-keyword")).not.toBeNull();
  expect(view.container.querySelector("script")).toBeNull();
  view.rerender(
    <MessageText text={`\`\`\`unknown-language\n${source}\`\`\``} />,
  );
  expect(view.container.querySelector("pre code")?.textContent).toBe(source);
  expect(view.container.querySelector(".hljs-keyword")).toBeNull();
});

it("retains highlighted code and copy state while later prose streams", async () => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  const prefix = "```typescript\nconst value = 1;\n```\n\n";
  const view = render(<MessageText text={prefix} />);
  const keyword = view.container.querySelector(".hljs-keyword");
  fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
  await screen.findByText("Copied");
  for (let index = 1; index <= 30; index++)
    view.rerender(<MessageText text={prefix + "streaming ".repeat(index)} />);
  expect(highlights).toHaveBeenCalledTimes(1);
  expect(view.container.querySelector(".hljs-keyword")).toBe(keyword);
  expect(screen.getByText("Copied")).toBeTruthy();
  view.rerender(
    <MessageText text={prefix.replace("value = 1", "value = 2")} />,
  );
  expect(highlights).toHaveBeenCalledTimes(2);
  expect(view.container.querySelector("pre code")?.textContent).toBe(
    "const value = 2;\n",
  );
});

it("keeps guest transcript links readable without opening owner pages or Host previews", () => {
  render(
    <GuestAccessContext
      value={{
        title: "Shared",
        grant: {
          share_id: "share-one",
          scope: "thread",
          target_id: "thread-one",
          permission: "view",
          created_at: "2026-10-19",
          expires_at: "2026-10-20",
        },
      }}
    >
      <MessageText text="[Report](/threads/other?native=files&native_path=%2Ftmp%2Freport.png) [Settings](/settings) [Broken](http://[) [External](https://example.com)" />
    </GuestAccessContext>,
  );
  expect(screen.getByText("Report").tagName).toBe("SPAN");
  expect(screen.getByText("Settings").tagName).toBe("SPAN");
  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(
    screen.getByRole("link", { name: "External" }).getAttribute("href"),
  ).toBe("https://example.com");
});
