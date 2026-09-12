import { useEffect, useRef } from "react";
import { basicSetup, EditorView } from "codemirror";
import { yaml } from "@codemirror/lang-yaml";

export function SourceEditor({
  value,
  onChange,
  label = "YAML source",
  readOnly = false,
}: {
  value: string;
  onChange?: (value: string) => void;
  label?: string;
  readOnly?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({
      parent: host.current,
      doc: value,
      extensions: [
        basicSetup,
        yaml(),
        EditorView.lineWrapping,
        EditorView.editable.of(!readOnly),
        EditorView.contentAttributes.of({ "aria-label": label }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) change.current?.(update.state.doc.toString());
        }),
        EditorView.theme({
          "&": {
            fontSize: "13px",
            backgroundColor: "var(--a13n-canvas)",
            color: "var(--a13n-text)",
          },
          ".cm-content": { fontFamily: "var(--a13n-mono)", minHeight: "240px" },
          ".cm-gutters": {
            backgroundColor: "var(--a13n-surface)",
            color: "var(--a13n-secondary)",
            border: "none",
          },
          ".cm-activeLine, .cm-activeLineGutter": {
            backgroundColor: "var(--a13n-surface)",
          },
          ".cm-cursor": { borderLeftColor: "var(--a13n-text)" },
          ".cm-scroller": { maxHeight: "560px", overflow: "auto" },
        }),
      ],
    });
    editor.current = view;
    return () => {
      view.destroy();
      editor.current = null;
    };
    // External values are synchronized below without replacing editor state on typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label, readOnly]);
  useEffect(() => {
    const view = editor.current;
    if (view && value !== view.state.doc.toString())
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
  }, [value]);
  return <div ref={host} />;
}
