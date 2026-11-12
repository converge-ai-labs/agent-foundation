import type { RefObject } from "react";
import type { ReplyQuote } from "./reply-quote";

export type DictationTarget = {
  words: number;
  title?: string;
  threadId?: string;
  reader?: RefObject<HTMLElement | null>;
};

const segmenter = new Intl.Segmenter(undefined, { granularity: "word" });

function takeWords(text: string, budget: number, backwards: boolean) {
  if (!budget) return { text: "", used: 0 };
  const segments = segmenter.segment(text);
  let used = 0;
  if (backwards) {
    let offset = text.length;
    while (offset > 0) {
      const part = segments.containing(offset - 1)!;
      offset = part.index;
      if (part.isWordLike && ++used === budget)
        return { text: text.slice(offset), used };
    }
  } else {
    for (const part of segments) {
      if (part.isWordLike && ++used === budget)
        return { text: text.slice(0, part.index + part.segment.length), used };
    }
  }
  return { text, used };
}

// Only authored input and reply prose carry these markers.
export function dictationContext(target: DictationTarget, quote?: ReplyQuote) {
  const { words } = target;
  const reader = target.reader?.current;
  if (!reader || !words) return quote?.text ?? "";
  const nodes = [
    ...reader.querySelectorAll<HTMLElement>(
      "[data-dictation-human], [data-reply-source]",
    ),
  ];
  const box = reader.getBoundingClientRect();
  const mid = (box.top + box.bottom) / 2;
  let index = quote
    ? nodes.findIndex((el) => el.dataset.replySource === quote.source)
    : -1;
  let offset = quote?.start ?? 0;
  if (!quote) {
    let nearest = Infinity;
    nodes.forEach((node, i) => {
      if (node.closest("[hidden], [inert]")) return;
      const r = node.getBoundingClientRect();
      if (!r.height) return;
      const dist = Math.max(r.top - mid, mid - r.bottom, 0);
      if (dist < nearest) {
        nearest = dist;
        index = i;
        offset = Math.round(
          (node.textContent?.length ?? 0) *
            Math.max(0, Math.min(1, (mid - r.top) / r.height)),
        );
      }
    });
  }
  if (index < 0) return quote?.text ?? "";
  function side(backwards: boolean) {
    let remaining = words;
    const rows: string[] = [];
    for (
      let i = index;
      remaining && i >= 0 && i < nodes.length;
      i += backwards ? -1 : 1
    ) {
      const node = nodes[i];
      let text = node.textContent ?? "";
      if (i === index)
        text = backwards
          ? text.slice(0, offset)
          : text.slice(offset + (quote?.raw.length ?? 0));
      const slice = takeWords(text, remaining, backwards);
      if (slice.text.trim())
        rows.push(
          (node.hasAttribute("data-dictation-human")
            ? "Human: "
            : "Assistant: ") + slice.text.trim(),
        );
      remaining -= slice.used;
    }
    return backwards ? rows.reverse() : rows;
  }
  return [
    ...side(true),
    ...(quote ? ["Selected assistant text: " + quote.text] : []),
    ...side(false),
  ].join("\n");
}
