import type { Schema } from "../transport/client";

// Only source-identical text nodes receive anchors. Decoded entities, escapes and
// generated Markdown punctuation deliberately fall back to original-source selection.
type SourceNode = {
  type: string;
  value?: string;
  position?: { start: { offset?: number }; end: { offset?: number } };
  children?: SourceNode[];
};
export function sourceAnchors(text: string) {
  return () => (tree: SourceNode) => {
    const visit = (node: SourceNode) => {
      if (!node.children) return;
      node.children = node.children.map((child) => {
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        if (
          child.type === "text" &&
          start !== undefined &&
          end !== undefined &&
          text.slice(start, end) === child.value
        ) {
          return {
            type: "element",
            tagName: "span",
            properties: { "data-source-start": start, "data-source-end": end },
            children: [child],
          };
        }
        visit(child);
        return child;
      });
    };
    visit(tree);
  };
}
function point(node: Node, offset: number, container: HTMLElement) {
  const element = node instanceof Element ? node : node.parentElement;
  const anchor = element?.closest<HTMLElement>("[data-source-start]");
  if (!anchor || !container.contains(anchor)) return undefined;
  const range = document.createRange();
  range.selectNodeContents(anchor);
  range.setEnd(node, offset);
  return Number(anchor.dataset.sourceStart) + range.toString().length;
}
export function selectedSource(
  container: HTMLElement,
  source: string,
  selection: Selection | null,
): Schema<"CommentSelection"> | undefined {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return;
  const range = selection.getRangeAt(0);
  const start = point(range.startContainer, range.startOffset, container);
  const end = point(range.endContainer, range.endOffset, container);
  if (start === undefined || end === undefined || start >= end) return;
  const quote = source.slice(start, end);
  if (quote !== range.toString() || !quote || [...quote].length > 16384) return;
  return {
    start: [...source.slice(0, start)].length,
    end: [...source.slice(0, end)].length,
    quote,
  };
}
