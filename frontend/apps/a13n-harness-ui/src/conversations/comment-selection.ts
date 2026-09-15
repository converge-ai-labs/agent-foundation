import type { Schema } from "../transport/client";

// Only source-identical text nodes receive anchors. Decoded entities, escapes and
// generated Markdown punctuation deliberately fall back to original-source selection.
type SourceNode = {
  type: string;
  value?: string;
  position?: { start: { offset?: number }; end: { offset?: number } };
  children?: SourceNode[];
};
export type CommentHighlight = {
  id: string;
  selection: Schema<"CommentSelection">;
};
export function sourceAnchors(
  text: string,
  highlights: CommentHighlight[] = [],
) {
  const characters = [...text];
  const ranges = highlights.flatMap(({ id, selection }) => {
    if (
      selection.start < 0 ||
      selection.end <= selection.start ||
      selection.end > characters.length ||
      characters.slice(selection.start, selection.end).join("") !==
        selection.quote
    )
      return [];
    return [
      {
        id,
        start: characters.slice(0, selection.start).join("").length,
        end: characters.slice(0, selection.end).join("").length,
      },
    ];
  });
  return () => (tree: SourceNode) => {
    const visit = (node: SourceNode) => {
      if (!node.children) return;
      node.children = node.children.flatMap<SourceNode>((child) => {
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        if (
          child.type === "text" &&
          start !== undefined &&
          end !== undefined &&
          text.slice(start, end) === child.value
        ) {
          const cuts = [
            ...new Set([
              start,
              end,
              ...ranges.flatMap((range) => [
                Math.max(start, Math.min(end, range.start)),
                Math.max(start, Math.min(end, range.end)),
              ]),
            ]),
          ].sort((a, b) => a - b);
          return cuts.slice(0, -1).map((from, index) => {
            const to = cuts[index + 1];
            const ids = ranges
              .filter((range) => range.start < to && range.end > from)
              .map((range) => range.id);
            return {
              type: "element",
              tagName: "span",
              properties: {
                "data-source-start": from,
                "data-source-end": to,
                ...(ids.length
                  ? {
                      "data-comment-ids": ids.join(" "),
                      tabIndex: 0,
                      role: "button",
                      "aria-label": "Read comments on highlighted text",
                    }
                  : {}),
              },
              children: [{ type: "text", value: text.slice(from, to) }],
            };
          });
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
  if (!quote || [...quote].length > 16384) return;
  if (quote !== range.toString()) {
    // Formatting may separate source-identical nodes. Verify every selected
    // text fragment in order; retain the exact source (including Markdown),
    // rather than searching for a repeated rendered quote.
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    let previous = start;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!range.intersectsNode(node)) continue;
      const from = node === range.startContainer ? range.startOffset : 0;
      const to =
        node === range.endContainer
          ? range.endOffset
          : (node.textContent?.length ?? 0);
      if (from === to) continue;
      const position = point(node, from, container);
      if (
        position === undefined ||
        position < previous ||
        position + to - from > end ||
        source.slice(position, position + to - from) !==
          node.textContent?.slice(from, to)
      )
        return;
      previous = position + to - from;
    }
    if (previous !== end) return;
  }
  return {
    start: [...source.slice(0, start)].length,
    end: [...source.slice(0, end)].length,
    quote,
  };
}
