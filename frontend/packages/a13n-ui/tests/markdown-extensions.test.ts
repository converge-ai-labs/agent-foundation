import { expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import { remarkClosedDiagram } from "../src/markdown-extensions";

it.each([
  ["```mermaid\nflowchart LR\n```", true],
  ["> ~~~~mermaid\n> flowchart LR\n> ~~~~", true],
  ["- ```mermaid\n  flowchart LR\n  ```", true],
  ["````mermaid\nflowchart LR\n```", false],
  ["```mermaid\nflowchart LR\n~~~", false],
  ["```mermaid\nflowchart LR", false],
  ["```text\nflowchart LR\n```", false],
])("records a closing Mermaid fence in %j", (text, closed) => {
  const processor = unified().use(remarkParse).use(remarkClosedDiagram);
  const tree = processor.parse(text);
  expect(JSON.stringify(tree).includes("dataClosedDiagram")).toBe(closed);
});
