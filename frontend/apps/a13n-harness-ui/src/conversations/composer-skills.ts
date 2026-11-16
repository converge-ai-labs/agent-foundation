import { StateEffect, StateField, type Range } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  WidgetType,
  keymap,
} from "@codemirror/view";
import { inlinePattern } from "./inline-attachments";
import {
  skillDisplayName,
  skillSpans,
  type SkillCatalog,
} from "./skill-references";
import styles from "./skill-chip.module.css";

class SkillWidget extends WidgetType {
  constructor(readonly name: string) {
    super();
  }
  eq(other: SkillWidget) {
    return other.name === this.name;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = styles.chip;
    span.dataset.skill = this.name;
    span.title = `$${this.name}`;
    span.textContent = skillDisplayName(this.name);
    return span;
  }
}

export const refreshSkills = StateEffect.define<SkillCatalog | undefined>();
const catalogField = StateField.define<SkillCatalog | undefined>({
  create: () => undefined,
  update: (catalog, transaction) => {
    for (const effect of transaction.effects)
      if (effect.is(refreshSkills)) catalog = effect.value;
    return catalog;
  },
});

function skillWidgets(text: string, catalog?: SkillCatalog): DecorationSet {
  if (!catalog || !text.includes("$")) return Decoration.none;
  const ranges: Range<Decoration>[] = [];
  let offset = 0;
  const append = (end: number) => {
    const part = text.slice(offset, end);
    const points = [...part];
    for (const span of skillSpans(part, catalog)) {
      const from = offset + points.slice(0, span.start).join("").length;
      ranges.push(
        Decoration.replace({ widget: new SkillWidget(span.name) }).range(
          from,
          from + span.name.length + 1,
        ),
      );
    }
  };
  for (const match of text.matchAll(inlinePattern)) {
    append(match.index);
    offset = match.index + match[0].length;
  }
  append(text.length);
  return Decoration.set(ranges);
}

const references = StateField.define<DecorationSet>({
  create: (state) =>
    skillWidgets(state.doc.toString(), state.field(catalogField)),
  update: (ranges, tr) =>
    tr.docChanged || tr.effects.some((e) => e.is(refreshSkills))
      ? skillWidgets(tr.newDoc.toString(), tr.state.field(catalogField))
      : ranges,
  provide: (field) => [
    EditorView.decorations.from(field),
    EditorView.atomicRanges.of((view) => view.state.field(field)),
  ],
});

// Completion inserts a separator so the next word stays outside the reference.
// Backspacing at that separator removes the reference in the same keystroke.
export const composerSkills = [
  catalogField,
  references,
  EditorView.inputHandler.of((view, from, to, text) => {
    if (
      from !== to ||
      view.compositionStarted ||
      (text !== "$" && text !== "/")
    )
      return false;
    const previous = view.state.field(references).iter(from);
    if (!previous.value || previous.to !== from) return false;
    // At the visible edge of an atom the user can start the next reference,
    // even if they clicked before the separator that completion inserted.
    view.dispatch({
      changes: { from, insert: " " + text },
      selection: { anchor: from + 2 },
      userEvent: "input.type",
    });
    return true;
  }),
  keymap.of([
    {
      key: "Backspace",
      run(view) {
        const { state } = view;
        const { head, empty } = state.selection.main;
        if (
          !empty ||
          state.selection.ranges.length !== 1 ||
          head < 2 ||
          state.sliceDoc(head - 1, head) !== " "
        )
          return false;
        const token = state.field(references).iter(head - 1);
        if (!token.value || token.to !== head - 1) return false;
        view.dispatch({
          changes: { from: token.from, to: head },
          userEvent: "delete.backward",
          scrollIntoView: true,
        });
        return true;
      },
    },
  ]),
];
