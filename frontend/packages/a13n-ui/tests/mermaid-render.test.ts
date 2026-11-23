// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { renderDiagram } from "../src/mermaid-render";

const { load, layout } = vi.hoisted(() => ({ load: vi.fn(), layout: vi.fn() }));
vi.mock("mermaid", () => {
  load();
  return { default: { initialize: vi.fn(), render: layout } };
});

it("does not import Mermaid for a rejected first preview", async () => {
  await expect(renderDiagram("pie title Source only", false)).rejects.toThrow(
    "source view",
  );
  expect(load).not.toHaveBeenCalled();
});

it.each([
  "---\ntitle: Custom configuration\n---\nflowchart LR\nA --> B",
  "%%{init: {}}%%\nflowchart LR\nA --> B",
  "flowchart LR\nA@{ shape: rect }",
  "flowchart LR; A --> B; classDef note fill:red",
  "stateDiagram-v2\nstyle A fill:red\nA --> B",
  "sequenceDiagram\nproperties Actor: {}",
  "sequenceDiagram\ndetails Actor: {}",
  "sequenceDiagram\n\u00a0PrOpErTiEs Actor: {}",
  "sequenceDiagram\nrect rgb(220, 230, 240)\nA->>B: Hello\nend",
  "stateDiagram-v2 style A fill:red\nA --> B",
  "sequenceDiagram\nA->>B: $$x$$",
  "flowchart LR\nA[<strong>Label</strong>] --> B",
  "pie title Source only\nA: 1",
  "flowchart LR\n" + " ".repeat(50_000),
])(
  "leaves unsupported diagram source out of the live layout (%#)",
  async (source) => {
    layout.mockClear();
    await expect(renderDiagram(source, false)).rejects.toThrow("source view");
    expect(layout).not.toHaveBeenCalled();
  },
);

it.each([
  ["Chinese flowchart", 'flowchart LR\nA["开始"] --> B["完成"]'],
  ["bidirectional flowchart", "flowchart LR\nA <--> B"],
  ["sequence message", "sequenceDiagram\nA-->>B: 已完成"],
  [
    "state transition and fork",
    "stateDiagram-v2\n[*] --> A\nstate A <<fork>>\nA --> B\nA --> C",
  ],
])("passes a plain %s unchanged to Mermaid layout", async (_name, source) => {
  layout.mockResolvedValue({
    svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 12"/>',
  });
  const image = await renderDiagram(source, false);
  expect(layout).toHaveBeenLastCalledWith(
    expect.any(String),
    source,
    expect.any(HTMLDivElement),
  );
  expect(image).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
});
