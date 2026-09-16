import { useEffect, useRef } from "react";
import { basicSetup, EditorView } from "codemirror";
import {
  Annotation,
  Compartment,
  EditorState,
  type Extension,
} from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { gotoLine } from "@codemirror/search";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";

export type EditorPosition = {
  anchor: number;
  head: number;
  top: number;
  left: number;
};

function fileLanguage(path: string): Extension {
  const extension = path.split(".").at(-1)?.toLowerCase();
  if (["js", "jsx", "mjs", "cjs", "ts", "tsx"].includes(extension ?? ""))
    return javascript({
      typescript: extension === "ts" || extension === "tsx",
      jsx: extension === "jsx" || extension === "tsx",
    });
  if (extension === "py" || extension === "pyi") return python();
  if (extension === "json") return json();
  if (extension === "md" || extension === "markdown") return markdown();
  if (extension === "yaml" || extension === "yml") return yaml();
  return [];
}

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
  filename,
  line,
  position,
  onPosition,
  onSave,
}: {
  value: string;
  onChange?: (value: string) => void;
  label?: string;
  readOnly?: boolean;
  language?: "yaml" | "plain";
  fill?: boolean;
  extensions?: Extension;
  wrap?: boolean;
  filename?: string;
  line?: number;
  position?: EditorPosition;
  onPosition?: (position: EditorPosition) => void;
  onSave?: () => void;
  onSelection?: (
    range: { start_line: number; end_line: number } | undefined,
  ) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const wrapMode = useRef(new Compartment());
  const save = useRef(onSave);
  save.current = onSave;
  const remember = useRef(onPosition);
  remember.current = onPosition;
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
        filename ? fileLanguage(filename) : language === "yaml" ? yaml() : [],
        wrapMode.current.of(wrap ? EditorView.lineWrapping : []),
        keymap.of([
          {
            key: "Mod-s",
            run: () => {
              if (!save.current) return false;
              save.current();
              return true;
            },
          },
          { key: "Mod-g", run: gotoLine },
        ]),
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
    if (position) {
      view.dispatch({
        selection: {
          anchor: Math.min(position.anchor, view.state.doc.length),
          head: Math.min(position.head, view.state.doc.length),
        },
        effects: EditorView.scrollIntoView(
          Math.min(position.head, view.state.doc.length),
        ),
      });
      view.requestMeasure({
        read: () => position,
        write: (saved) => {
          view.scrollDOM.scrollTop = saved.top;
          view.scrollDOM.scrollLeft = saved.left;
        },
      });
    }
    return () => {
      remember.current?.({
        anchor: view.state.selection.main.anchor,
        head: view.state.selection.main.head,
        top: view.scrollDOM.scrollTop,
        left: view.scrollDOM.scrollLeft,
      });
      view.destroy();
      editor.current = null;
    };
    // External values are synchronized below without replacing editor state on typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label, readOnly, language, filename, fill, extensions]);
  useEffect(() => {
    editor.current?.dispatch({
      effects: wrapMode.current.reconfigure(
        wrap ? EditorView.lineWrapping : [],
      ),
    });
  }, [wrap]);
  useEffect(() => {
    const view = editor.current;
    if (!view || line === undefined) return;
    const target = view.state.doc.line(
      Math.max(1, Math.min(line, view.state.doc.lines)),
    ).from;
    view.dispatch({
      selection: { anchor: target },
      effects: EditorView.scrollIntoView(target, { y: "center" }),
    });
  }, [line]);
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
