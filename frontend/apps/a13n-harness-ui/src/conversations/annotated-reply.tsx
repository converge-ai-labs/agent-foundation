import {
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "../i18n";
import { AnnotationContext } from "./annotation-context";
import { quoteRange, type ReplyAnnotation } from "./reply-quote";
import styles from "./reply-annotations.module.css";

type Mark = { id: string; left: number; top: number };

// Anchor to the first selected line. A single line can contain several client
// rects when the selection crosses a link, bold text, or inline code.
function placeMarks(el: HTMLElement, notes: readonly ReplyAnnotation[]) {
  const box = el.parentElement!.getBoundingClientRect();
  const step = window.matchMedia("(pointer: coarse)").matches ? 44 : 22;
  const placed: Mark[] = [];
  for (const note of notes) {
    const range = quoteRange(el, note);
    if (!range) continue;
    const rects = [...range.getClientRects()].filter(
      (r) => r.width && r.height,
    );
    const first = rects[0];
    if (!first) continue;
    const mid = first.top + first.height / 2;
    const line = rects.filter(
      (r) => r.top <= mid && r.bottom >= mid && r.height < first.height * 1.75,
    );
    const target = Math.min(
      box.width - 9,
      Math.max(9, Math.max(...line.map((r) => r.right)) - box.left),
    );
    let top = Math.round(first.top - box.top - 2);
    // Keep nearby numbers individually clickable, choosing the closest free
    // spot to the quote rather than making a rail at the edge of the reply.
    for (;;) {
      const neighbors = placed.filter((m) => Math.abs(m.top - top) < step);
      const choices = [
        target,
        ...neighbors.flatMap((m) => [m.left + step, m.left - step]),
      ]
        .filter(
          (x) =>
            x >= 9 &&
            x <= box.width - 9 &&
            neighbors.every((m) => Math.abs(m.left - x) >= step),
        )
        .sort((a, b) => Math.abs(a - target) - Math.abs(b - target));
      if (choices.length || box.width < 18) {
        placed.push({
          id: note.id,
          left: Math.round(choices[0] ?? target),
          top,
        });
        break;
      }
      top -= step;
    }
  }
  return placed;
}

export function AnnotatedReply({
  source,
  slot,
  children,
}: {
  source: string;
  slot?: string;
  children: ReactNode;
}) {
  const ctx = useContext(AnnotationContext);
  const root = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  const [marks, setMarks] = useState<Mark[]>([]);
  const notes = ctx?.notes;
  const mine = useMemo(
    () =>
      notes?.filter((note) => note.source === source || note.source === slot),
    [notes, source, slot],
  );
  useLayoutEffect(() => {
    const el = root.current;
    if (!el || !mine?.length) {
      setMarks((old) => (old.length ? [] : old));
      return;
    }
    const measure = () => {
      const next = placeMarks(el, mine);
      setMarks((old) =>
        old.length === next.length &&
        old.every(
          (m, i) =>
            m.id === next[i].id &&
            m.top === next[i].top &&
            m.left === next[i].left,
        )
          ? old
          : next,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    const content = new MutationObserver(measure);
    content.observe(el, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    el.addEventListener("scroll", measure, true);
    return () => {
      el.removeEventListener("scroll", measure, true);
      observer.disconnect();
      content.disconnect();
    };
  }, [mine]);
  if (!ctx) return children;
  return (
    <div className={styles.reply} data-marked={mine?.length || undefined}>
      <div data-reply-source={source} data-reply-slot={slot} ref={root}>
        {children}
      </div>
      {marks.map((mark) => {
        const index = ctx.notes.findIndex((note) => note.id === mark.id);
        if (index < 0) return null;
        const label = t("Edit annotation {{number}}", { number: index + 1 });
        return (
          <button
            key={mark.id}
            type="button"
            className={styles.marker}
            style={{ top: mark.top, left: mark.left }}
            data-reply-marker={mark.id}
            data-active={ctx.active === mark.id || undefined}
            aria-label={label}
            title={label}
            onClick={(e) => ctx.edit(ctx.notes[index], e.currentTarget)}
          >
            {index + 1}
          </button>
        );
      })}
    </div>
  );
}
