import type { OrderedInputPart } from "./inline-attachments";

export type ReplyQuote = {
  source: string;
  start: number;
  // DOM ranges omit some rendered paragraph and table separators.
  raw: string;
  text: string;
};

export type ReplyAnnotation = ReplyQuote & { id: string; comment: string };

export function withReplyAnnotations(
  parts: OrderedInputPart[],
  notes: readonly ReplyAnnotation[],
): OrderedInputPart[] {
  if (!notes.length) return parts;
  const context = notes
    .map(({ text, comment }) => {
      const quote = text
        .split(/\r?\n/)
        .map((line) => `> ${line}`)
        .join("\n");
      return comment ? `${quote}\n\n${comment}` : quote;
    })
    .join("\n\n");
  return [...parts, (parts.length ? "\n\n" : "") + context];
}

const elementOf = (node: Node) =>
  node instanceof Element ? node : node.parentElement;
const decoration = "[data-reply-decoration]";

function proseText(range: Range) {
  const fragment = range.cloneContents();
  for (const node of fragment.querySelectorAll(decoration)) node.remove();
  return fragment.textContent ?? "";
}

export function selectedQuote(
  reader: HTMLElement,
  selection: Selection | null,
) {
  if (!selection || selection.rangeCount !== 1 || selection.isCollapsed) return;
  const range = selection.getRangeAt(0);
  const source = elementOf(range.commonAncestorContainer)?.closest<HTMLElement>(
    "[data-reply-source]",
  );
  if (!source || !reader.contains(source)) return;
  if (
    elementOf(range.startContainer)?.closest(
      `button, textarea, input, ${decoration}`,
    ) ||
    elementOf(range.endContainer)?.closest(
      `button, textarea, input, ${decoration}`,
    )
  )
    return;
  const raw = proseText(range);
  const text = range.cloneContents().querySelector(decoration)
    ? raw.trim()
    : selection.toString().trim();
  if (!raw.trim() || !text) return;
  const before = document.createRange();
  before.selectNodeContents(source);
  before.setEnd(range.startContainer, range.startOffset);
  return {
    quote: {
      source: source.dataset.replySource!,
      start: proseText(before).length,
      raw,
      text,
    },
    range: range.cloneRange(),
    element: source,
  };
}

export function quoteRange(
  root: HTMLElement,
  quote: ReplyQuote,
): Range | undefined {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      elementOf(node)?.closest(decoration)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  let offset = 0;
  let range: Range | undefined;
  for (
    let node = walker.nextNode() as Text | null;
    node;
    node = walker.nextNode() as Text | null
  ) {
    const next = offset + node.length;
    if (!range && quote.start >= offset && quote.start < next) {
      range = document.createRange();
      range.setStart(node, quote.start - offset);
    }
    const end = quote.start + quote.raw.length;
    if (range && end <= next) {
      range.setEnd(node, end - offset);
      return proseText(range) === quote.raw ? range : undefined;
    }
    offset = next;
  }
}
