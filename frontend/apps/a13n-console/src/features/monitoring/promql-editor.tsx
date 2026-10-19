import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { PromQLExtension } from "@prometheus-io/codemirror-promql";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";
import styles from "./monitoring.module.css";

/** Syntax, completion and linting stay local; source credentials never enter the browser. */
export function PromQLEditor({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null),
    view = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  useEffect(() => {
    const promql = new PromQLExtension();
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          promql.asExtension(),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.theme({
            "&": {
              background: "var(--a13n-surface)",
              color: "var(--a13n-text)",
              borderRadius: "8px",
            },
            ".cm-content": { minHeight: "88px", fontFamily: "monospace" },
            ".cm-gutters": { background: "transparent", border: "none" },
            ".cm-scroller": { maxHeight: "220px", overflow: "auto" },
          }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) change.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      view.current = null;
      editor.destroy();
      promql.destroy();
    };
  }, [label]);
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value)
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: value },
      });
  }, [value]);
  return <div ref={host} className={styles.editor} />;
}
