import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
} from "react";
import { createPortal } from "react-dom";
import { fileHighlighting } from "./file-highlighting";
import { FileSearch } from "./file-search";
import { useAppearance } from "../appearance/react";
import { basicSetup, EditorView } from "codemirror";
import {
  Annotation,
  Compartment,
  EditorState,
  type Extension,
} from "@codemirror/state";
import { keymap } from "@codemirror/view";
import {
  gotoLine,
  openSearchPanel,
  search,
  getSearchQuery,
  type SearchQuery,
} from "@codemirror/search";
import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";

const sourceHighlighting = syntaxHighlighting(
  HighlightStyle.define([
    {
      tag: [tags.keyword, tags.bool, tags.null],
      color: "var(--harness-code-keyword)",
    },
    { tag: [tags.string, tags.regexp], color: "var(--harness-code-string)" },
    {
      tag: [tags.number, tags.atom, tags.propertyName, tags.typeName],
      color: "var(--harness-code-value)",
    },
    {
      tag: tags.definition(tags.variableName),
      color: "var(--harness-code-definition)",
    },
    {
      tag: tags.comment,
      color: "var(--harness-code-comment)",
      fontStyle: "italic",
    },
    {
      tag: tags.heading,
      color: "var(--harness-code-definition)",
      fontWeight: "600",
    },
    {
      tag: tags.link,
      color: "var(--harness-code-value)",
      textDecoration: "underline",
    },
    { tag: tags.emphasis, fontStyle: "italic" },
    { tag: tags.strong, fontWeight: "600" },
    { tag: tags.deleted, color: "var(--harness-code-deletion)" },
    { tag: tags.inserted, color: "var(--harness-code-string)" },
  ]),
);

export type SourceEditorHandle = {
  focus: () => void;
  find: () => void;
  goToLine: () => void;
};

export type EditorCursor = {
  line: number;
  column: number;
  selected: number;
};

export type EditorPosition = {
  anchor: number;
  head: number;
  top: number;
  left: number;
};

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
  ref,
  onCursor,
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
  ref?: Ref<SourceEditorHandle>;
  onCursor?: (cursor: EditorCursor) => void;
  onSelection?: (
    range: { start_line: number; end_line: number } | undefined,
  ) => void;
}) {
  const [panel, setPanel] = useState<{
    view: EditorView;
    dom: HTMLElement;
    query: SearchQuery;
  } | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const wrapMode = useRef(new Compartment());
  const { appearance } = useAppearance();
  const contentAttributes = useRef(new Compartment());
  const cursor = useRef(onCursor);
  cursor.current = onCursor;
  useImperativeHandle(
    ref,
    () => ({
      focus: () => editor.current?.focus(),
      find: () => {
        if (editor.current) openSearchPanel(editor.current);
      },
      goToLine: () => {
        if (editor.current) gotoLine(editor.current);
      },
    }),
    [],
  );
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
    const reportCursor = (state: EditorState) => {
      const range = state.selection.main;
      const line = state.doc.lineAt(range.head);
      cursor.current?.({
        line: line.number,
        column: range.head - line.from + 1,
        selected: state.selection.ranges.reduce(
          (total, range) => total + range.to - range.from,
          0,
        ),
      });
    };
    const view = new EditorView({
      parent: host.current,
      doc: value,
      extensions: [
        filename
          ? fileHighlighting(filename)
          : [sourceHighlighting, language === "yaml" ? yaml() : []],
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
        filename
          ? search({
              top: true,
              createPanel(view) {
                const dom = document.createElement("div");
                const publish = () =>
                  setPanel({ view, dom, query: getSearchQuery(view.state) });
                return {
                  dom,
                  top: true,
                  mount: publish,
                  update(update) {
                    if (
                      update.docChanged ||
                      update.selectionSet ||
                      getSearchQuery(update.startState) !==
                        getSearchQuery(update.state)
                    )
                      publish();
                  },
                  destroy() {
                    setPanel(null);
                  },
                };
              },
            })
          : [],
        basicSetup,
        EditorView.editable.of(!readOnly),
        EditorState.readOnly.of(readOnly),
        contentAttributes.current.of(
          EditorView.contentAttributes.of({
            "aria-label": label,
            tabindex: "0",
          }),
        ),
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
            reportCursor(update.state);
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
            fontSize: "var(--harness-code-size, 12px)",
            ...(fill
              ? { height: filename ? "auto" : "100%" }
              : {
                  border: "1px solid var(--a13n-border)",
                  borderRadius: "6px",
                  overflow: "hidden",
                }),
            backgroundColor: filename
              ? "var(--a13n-surface)"
              : "var(--a13n-canvas)",
            color: "var(--a13n-text)",
          },
          "&.cm-focused": {
            outline: fill ? "none" : "2px solid var(--a13n-signal-soft)",
            outlineOffset: "1px",
          },
          ".cm-content": {
            padding: filename ? "12px 0" : "8px 0",
            minHeight: filename ? "0" : fill ? "100%" : "160px",
          },
          ".cm-line": { padding: "0 12px" },
          ".cm-gutters": {
            backgroundColor: "var(--a13n-surface)",
            color: "var(--a13n-secondary)",
            border: "none",
          },
          // The selection layer sits behind the text: an opaque active line
          // hides mouse/keyboard selections on that line.
          ".cm-activeLine": {
            backgroundColor: filename ? "transparent" : "var(--a13n-hover)",
          },
          ".cm-activeLineGutter": {
            backgroundColor: filename ? "transparent" : "var(--a13n-selected)",
          },
          ".cm-cursor": { borderLeftColor: "var(--a13n-text)" },
          ".cm-selectionBackground": {
            backgroundColor:
              "color-mix(in srgb, var(--a13n-signal) 16%, transparent)",
          },
          "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground":
            {
              backgroundColor:
                "color-mix(in srgb, var(--a13n-signal) 26%, transparent)",
            },
          ".cm-panels, .cm-tooltip": {
            backgroundColor: "var(--a13n-elevated)",
            color: "var(--a13n-text)",
            borderColor: "var(--a13n-border)",
          },
          ".cm-panel.cm-search": {
            fontSize: "var(--a13n-text-body)",
            padding: "8px 32px 8px 8px",
            lineHeight: "2.4",
          },
          ".cm-panel.cm-search input[type=checkbox]": {
            accentColor: "var(--a13n-signal)",
          },
          ".cm-searchMatch": {
            backgroundColor: "var(--a13n-signal-soft)",
            outline: "1px solid var(--a13n-signal-soft)",
          },
          ".cm-searchMatch-selected": {
            backgroundColor:
              "color-mix(in srgb, var(--a13n-signal) 30%, transparent)",
          },
          ".cm-textfield, .cm-button": {
            background: "var(--a13n-surface)",
            color: "var(--a13n-text)",
            border: "1px solid var(--a13n-border)",
            borderRadius: "4px",
            minHeight: "32px",
            padding: "4px 8px",
            font: "inherit",
          },
          ".cm-button:hover": { background: "var(--a13n-hover)" },
          ".cm-foldPlaceholder": {
            backgroundColor: "var(--a13n-surface)",
            color: "var(--a13n-secondary)",
            borderColor: "var(--a13n-border)",
          },
          ".cm-scroller": {
            fontFamily: "var(--a13n-mono)",
            lineHeight: "1.65",
            maxHeight: filename ? "72dvh" : fill ? "none" : "min(400px, 50dvh)",
            overflow: "auto",
            overscrollBehavior: "contain",
          },
          "@media (max-width: 640px) and (pointer: coarse)": {
            "&": { fontSize: "max(16px, var(--harness-code-size, 12px))" },
          },
        }),
        extensions ?? [],
      ],
    });
    editor.current = view;
    reportCursor(view.state);
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
  }, [readOnly, language, filename, fill, extensions]);
  useEffect(() => {
    editor.current?.requestMeasure();
  }, [appearance.sizes.code]);
  useEffect(() => {
    editor.current?.dispatch({
      effects: contentAttributes.current.reconfigure(
        EditorView.contentAttributes.of({ "aria-label": label, tabindex: "0" }),
      ),
    });
  }, [label]);
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
    <>
      {panel &&
        createPortal(
          <FileSearch view={panel.view} query={panel.query} />,
          panel.dom,
        )}
      <div
        ref={host}
        style={fill ? { height: "100%", minHeight: 0 } : undefined}
      />
    </>
  );
}
