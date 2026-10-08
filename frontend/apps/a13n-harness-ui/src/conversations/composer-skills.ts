import { StateEffect, StateField, type Range } from "@codemirror/state";
import { Decoration, EditorView } from "@codemirror/view";
import { inlinePattern } from "./inline-attachments";
import { skillSpans, type SkillCatalog } from "./skill-references";
import styles from "./skill-chip.module.css";

export const refreshSkills = StateEffect.define<SkillCatalog | undefined>();
export const composerSkills = StateField.define<SkillCatalog | undefined>({
  create: () => undefined,
  update: (catalog, transaction) => {
    for (const effect of transaction.effects)
      if (effect.is(refreshSkills)) catalog = effect.value;
    return catalog;
  },
  provide: (field) =>
    EditorView.decorations.compute([field, "doc"], (state) => {
      const catalog = state.field(field);
      const text = state.doc.toString();
      const ranges: Range<Decoration>[] = [];
      let offset = 0;
      const append = (end: number) => {
        const part = text.slice(offset, end);
        const points = [...part];
        for (const span of skillSpans(part, catalog)) {
          const from = offset + points.slice(0, span.start).join("").length;
          ranges.push(
            Decoration.mark({
              class: styles.chip,
              attributes: {
                title: `Skill: ${span.name}`,
                "data-skill": span.name,
              },
            }).range(from, from + `$${span.name}`.length),
          );
        }
      };
      for (const match of text.matchAll(inlinePattern)) {
        append(match.index);
        offset = match.index + match[0].length;
      }
      append(text.length);
      return Decoration.set(ranges);
    }),
});
