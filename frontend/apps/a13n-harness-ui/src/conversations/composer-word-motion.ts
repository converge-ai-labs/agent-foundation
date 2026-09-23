import { EditorSelection, type SelectionRange } from "@codemirror/state";
import { Direction, type EditorView, type KeyBinding } from "@codemirror/view";

const words =
  typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter("zh", { granularity: "word" })
    : undefined;

function moveWord(view: EditorView, range: SelectionRange, forward: boolean) {
  const grouped = view.moveByGroup(range, forward);
  const line = view.state.doc.lineAt(range.head);
  const from = Math.min(range.head, grouped.head);
  const to = Math.max(range.head, grouped.head);
  // Refine only Chinese-containing groups. Keep CodeMirror's punctuation,
  // line crossing and bidi behavior rather than replacing all word navigation.
  if (
    !words ||
    from < line.from ||
    to > line.to ||
    view.bidiSpans(line).some((span) => span.level !== 0) ||
    !/\p{Script=Han}/u.test(view.state.sliceDoc(from, to))
  )
    return grouped;

  let boundary = grouped.head;
  for (const word of words.segment(line.text)) {
    if (!word.isWordLike) continue;
    const edge = line.from + word.index + (forward ? word.segment.length : 0);
    if (
      forward
        ? edge > range.head && edge < boundary
        : edge < range.head && edge > boundary
    )
      boundary = edge;
  }
  if (boundary === grouped.head) return grouped;
  const distance = Math.abs(boundary - range.head);
  // Use the public grapheme/atomic-range movement so attachment tokens remain
  // indivisible even when a linguistic boundary lies inside their hidden text.
  return view.moveByChar(range, forward, (first) => {
    let moved = first.length;
    return (next) => (moved += next.length) <= distance;
  });
}

function wordMotion(right: boolean, extend: boolean) {
  return (view: EditorView) => {
    if (view.compositionStarted) return false;
    const selection = EditorSelection.create(
      view.state.selection.ranges.map((range) => {
        const forward =
          (view.textDirectionAt(range.head) === Direction.LTR) === right;
        if (!extend && !range.empty)
          return EditorSelection.cursor(forward ? range.to : range.from);
        if (
          extend &&
          range.undirectional &&
          range.head >= range.anchor !== forward
        )
          range = EditorSelection.range(range.head, range.anchor);
        const next = moveWord(view, range, forward);
        return extend
          ? EditorSelection.range(
              range.anchor,
              next.head,
              next.goalColumn,
              next.bidiLevel ?? undefined,
              next.assoc,
            )
          : next;
      }),
      view.state.selection.mainIndex,
    );
    if (selection.eq(view.state.selection)) return false;
    view.dispatch({ selection, scrollIntoView: true, userEvent: "select" });
    return true;
  };
}

export const composerWordKeymap: KeyBinding[] = [
  {
    key: "Mod-ArrowLeft",
    mac: "Alt-ArrowLeft",
    run: wordMotion(false, false),
    shift: wordMotion(false, true),
    preventDefault: true,
  },
  {
    key: "Mod-ArrowRight",
    mac: "Alt-ArrowRight",
    run: wordMotion(true, false),
    shift: wordMotion(true, true),
    preventDefault: true,
  },
];
