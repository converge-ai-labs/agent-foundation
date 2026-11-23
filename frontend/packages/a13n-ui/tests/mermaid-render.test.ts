import { expect, it, vi } from "vitest";
import { renderDiagram } from "../src/mermaid-render";

const load = vi.hoisted(() => vi.fn());
vi.mock("mermaid", () => {
  load();
  throw new Error("Unsupported diagrams must not load Mermaid");
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
    await expect(renderDiagram(source, false)).rejects.toThrow("source view");
    expect(load).not.toHaveBeenCalled();
  },
);
