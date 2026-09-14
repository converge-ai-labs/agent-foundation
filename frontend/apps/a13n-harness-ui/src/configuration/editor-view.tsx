import { useEffect, useRef } from "react";
import { basicSetup, EditorView } from "codemirror";
import { Annotation, EditorState, type Extension } from "@codemirror/state";
import { yaml } from "@codemirror/lang-yaml";

const externalValue = Annotation.define<boolean>();

export function SourceEditor({
  value,
  onChange,
  label = "YAML source",
  readOnly = false,
  language = "yaml",
  onSelection,
  fill = false,
  extensions,
  wrap = true,
}: {
  value: string;
  onChange?: (value: string) => void;
  label?: string;
  readOnly?: boolean;
  language?: "yaml" | "plain";
  fill?: boolean;
  extensions?: Extension;
  wrap?: boolean;
  onSelection?: (
    range: { start_line: number; end_line: number } | undefined,
  ) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  const newline = useRef("\n");
  newline.current = value.match(/\r\n|\r|\n/)?.[0] ?? "\n";
  const selection = useRef(onSelection);
  selection.current = onSelection;
  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({
      parent: host.current,
      doc: value,
      extensions: [
        basicSetup,
        ...(language === "yaml" ? [yaml()] : []),
        ...(wrap ? [EditorView.lineWrapping] : []),
        EditorView.editable.of(!readOnly),
        EditorState.readOnly.of(readOnly),
        EditorView.contentAttributes.of({ "aria-label": label }),
        EditorView.updateListener.of((update) => {
          if (
            update.docChanged &&
            !update.transactions.some((transaction) =>
              transaction.annotation(externalValue),
            )
          )
            change.current?.(
              update.state.doc.sliceString(
                0,
                update.state.doc.length,
                newline.current,
              ),
            );
          if (update.selectionSet || update.docChanged) {
            const range = update.state.selection.main;
            selection.current?.(
              range.empty
                ? undefined
                : {
                    start_line: update.state.doc.lineAt(range.from).number,
                    end_line: update.state.doc.lineAt(
                      Math.max(range.from, range.to - 1),
                    ).number,
                  },
            );
          }
        }),
        EditorView.theme({
          "&": {
            fontSize: "13px",
            ...(fill ? { height: "100%" } : {}),
            backgroundColor: "var(--a13n-canvas)",
            color: "var(--a13n-text)",
          },
          ".cm-content": {
            fontFamily: "var(--a13n-mono)",
            minHeight: fill ? "100%" : "240px",
          },
          ".cm-gutters": {
            backgroundColor: "var(--a13n-surface)",
            color: "var(--a13n-secondary)",
            border: "none",
          },
          ".cm-activeLine, .cm-activeLineGutter": {
            backgroundColor: "var(--a13n-surface)",
          },
          ".cm-cursor": { borderLeftColor: "var(--a13n-text)" },
          ".cm-scroller": {
            maxHeight: fill ? "none" : "560px",
            overflow: "auto",
          },
        }),
        extensions ?? [],
      ],
    });
    editor.current = view;
    return () => {
      view.destroy();
      editor.current = null;
    };
    // External values are synchronized below without replacing editor state on typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label, readOnly, language, fill, extensions, wrap]);
  useEffect(() => {
    const view = editor.current;
    if (view && !view.state.toText(value).eq(view.state.doc))
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
        annotations: externalValue.of(true),
      });
  }, [value]);
  return (
    <div
      ref={host}
      style={fill ? { height: "100%", minHeight: 0 } : undefined}
    />
  );
}
