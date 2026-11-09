import {
  Decoration,
  EditorView,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";
import { StateEffect } from "@codemirror/state";
import { fileLanguage } from "./file-language";
import type { TokenRange } from "./file-highlight.worker";
const painted = StateEffect.define<DecorationSet>();
// Keep CodeMirror's selection, search and viewport. Paint the reviewed source
// with the same TextMate grammars and shared themes as documentation fences.
export function fileHighlighting(path: string) {
  return ViewPlugin.fromClass(
    class {
      decorations = Decoration.none;
      worker?: Worker;
      request = 0;
      constructor(readonly view: EditorView) {
        this.highlight();
      }
      highlight() {
        const doc = this.view.state.doc,
          language = fileLanguage(path, doc.line(1).text);
        if (!language || typeof Worker === "undefined") return;
        this.worker ??= new Worker(
          new URL("./file-highlight.worker.ts", import.meta.url),
          { type: "module" },
        );
        this.worker.onmessage = ({
          data,
        }: {
          data: { id: number; tokens: TokenRange[] };
        }) => {
          if (data.id !== this.request || this.view.state.doc !== doc) return;
          const marks = data.tokens.map(([from, to, light, dark]) =>
            Decoration.mark({
              attributes: { style: `color:light-dark(${light},${dark})` },
            }).range(from, to),
          );
          this.view.dispatch({ effects: painted.of(Decoration.set(marks)) });
        };
        this.worker.postMessage({
          id: ++this.request,
          language,
          code: doc.toString(),
        });
      }
      update(update: ViewUpdate) {
        if (update.docChanged) {
          this.decorations = Decoration.none;
          this.highlight();
        }
        for (const tr of update.transactions)
          for (const e of tr.effects)
            if (e.is(painted)) this.decorations = e.value;
      }
      destroy() {
        this.worker?.terminate();
      }
    },
    { decorations: (v) => v.decorations },
  );
}
