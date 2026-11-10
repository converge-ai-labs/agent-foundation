import {
  Compartment,
  EditorState,
  Text,
  type Extension,
} from "@codemirror/state";
import {
  EditorView,
  highlightWhitespace,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { searchKeymap } from "@codemirror/search";
import { tags } from "@lezer/highlight";
import {
  MergeView,
  goToNextChunk,
  goToPreviousChunk,
  unifiedMergeView,
} from "@codemirror/merge";
import { useEffect, useRef } from "react";
import { sourceLines } from "./text-model";
import styles from "./reader.module.css";

export type TextEditorProps = {
  path: string;
  text: string;
  before?: string;
  split?: boolean;
  wrap: boolean;
  collapse?: boolean;
  label: string;
  beforeLabel?: string;
  onNavigate?: (navigate: (forward: boolean) => void) => void;
};

const theme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "var(--a13n-text)",
  },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: "var(--a13n-mono)",
    fontSize: "12px",
    lineHeight: "1.75",
  },
  ".cm-content": { padding: "12px 0 24px", tabSize: "4" },
  ".cm-line": { padding: "0 16px 0 8px" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "var(--a13n-secondary)",
    border: "none",
    userSelect: "none",
  },
  ".cm-lineNumbers .cm-gutterElement": {
    minWidth: "36px",
    paddingRight: "10px",
  },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection":
    { background: "var(--a13n-selected)" },
  ".cm-deletedChunk, .cm-deletedLine": {
    backgroundColor: "color-mix(in srgb, var(--a13n-danger) 9%, transparent)",
  },
  ".cm-insertedLine": {
    backgroundColor: "color-mix(in srgb, var(--a13n-success) 9%, transparent)",
  },
  ".cm-deletedText": {
    backgroundColor: "color-mix(in srgb, var(--a13n-danger) 22%, transparent)",
    textDecoration: "none",
  },
  ".cm-insertedText": {
    backgroundColor: "color-mix(in srgb, var(--a13n-success) 22%, transparent)",
  },
  ".cm-changedLineGutter": {
    width: "4px",
    background: "var(--a13n-secondary)",
  },
  ".cm-changeGutter": { width: "10px" },
  ".cm-deletedLineGutter::after": {
    content: "'-'",
    color: "var(--a13n-danger)",
  },
  ".cm-insertedLineGutter::after": {
    content: "'+'",
    color: "var(--a13n-success)",
  },
  ".cm-collapsedLines": {
    margin: "4px 0",
    padding: "6px 12px",
    background: "var(--a13n-hover)",
    color: "var(--a13n-secondary)",
    border: "none",
    cursor: "pointer",
  },
  ".cm-search": {
    background: "var(--a13n-elevated)",
    color: "var(--a13n-text)",
  },
});

const highlighting = HighlightStyle.define([
  { tag: [tags.keyword, tags.bool, tags.null], color: "var(--a13n-accent)" },
  { tag: [tags.string, tags.regexp], color: "var(--success-foreground)" },
  {
    tag: [tags.number, tags.propertyName, tags.typeName],
    color: "var(--info-foreground)",
  },
  { tag: [tags.comment, tags.meta], color: "var(--a13n-secondary)" },
  { tag: tags.heading, fontWeight: "600" },
  { tag: tags.strong, fontWeight: "600" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, color: "var(--a13n-accent)", textDecoration: "underline" },
]);

/** Imperative CodeMirror lifetime stays inside the lazy reader chunk. */
export default function TextEditor({
  path,
  text,
  before,
  split,
  wrap,
  collapse = true,
  label,
  beforeLabel,
  onNavigate,
}: TextEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const options = useRef({ wrap, collapse });
  options.current = { wrap, collapse };
  const active = useRef<{
    views: EditorView[];
    merge?: MergeView;
    wrapping: Compartment;
    comparison: Compartment;
  }>(undefined);
  const position = useRef<{
    path: string;
    text: string;
    before?: string;
    selection: EditorState["selection"];
    scroll: ReturnType<EditorView["scrollSnapshot"]>;
  }>(undefined);
  useEffect(() => {
    if (!host.current) return;
    const language = /\.md$/i.test(path)
      ? markdown()
      : /\.json$/i.test(path)
        ? json()
        : /\.ya?ml$/i.test(path)
          ? yaml()
          : [];
    const wrapping = new Compartment();
    const comparison = new Compartment();
    const extensions: Extension[] = [
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
      EditorState.lineSeparator.of("\n"),
      EditorState.tabSize.of(4),
      lineNumbers(),
      theme,
      syntaxHighlighting(highlighting),
      language,
      keymap.of(searchKeymap),
      wrapping.of(options.current.wrap ? EditorView.lineWrapping : []),
      ...(before !== undefined ? [highlightWhitespace()] : []),
    ];
    const doc = (value: string) => Text.of(sourceLines(value));
    const state = (value: string, name: string) => ({
      doc: doc(value),
      extensions: [
        ...extensions,
        EditorView.contentAttributes.of({ "aria-label": name }),
      ],
    });
    const diffOptions = {
      highlightChanges: true,
      gutter: true,
      diffConfig: { scanLimit: 10000, timeout: 1000 },
      ...(options.current.collapse
        ? { collapseUnchanged: { margin: 3, minSize: 8 } }
        : {}),
    };
    function remember(view: EditorView) {
      position.current = {
        path,
        text,
        before,
        selection: view.state.selection,
        scroll: view.scrollSnapshot(),
      };
    }
    function restore(view: EditorView) {
      const saved = position.current;
      if (
        saved?.path === path &&
        saved.text === text &&
        saved.before === before
      )
        view.dispatch({ selection: saved.selection, effects: saved.scroll });
    }
    if (before !== undefined && split) {
      const view = new MergeView({
        parent: host.current,
        a: state(before, beforeLabel ?? label),
        b: state(text, label),
        ...diffOptions,
      });
      active.current = {
        views: [view.a, view.b],
        merge: view,
        wrapping,
        comparison,
      };
      restore(view.b);
      // Spacers align both documents; mirror vertical position, not horizontal selection.
      const syncA = () => {
        view.b.scrollDOM.scrollTop = view.a.scrollDOM.scrollTop;
      };
      const syncB = () => {
        view.a.scrollDOM.scrollTop = view.b.scrollDOM.scrollTop;
      };
      view.a.scrollDOM.addEventListener("scroll", syncA);
      view.b.scrollDOM.addEventListener("scroll", syncB);
      onNavigate?.((forward) => {
        (forward ? goToNextChunk : goToPreviousChunk)(view.b);
      });
      return () => {
        remember(view.b);
        active.current = undefined;
        view.a.scrollDOM.removeEventListener("scroll", syncA);
        view.b.scrollDOM.removeEventListener("scroll", syncB);
        view.destroy();
      };
    }
    const view = new EditorView({
      parent: host.current,
      ...state(text, label),
      extensions: [
        ...state(text, label).extensions,
        ...(before !== undefined
          ? [
              comparison.of(
                unifiedMergeView({
                  original: doc(before),
                  mergeControls: false,
                  ...diffOptions,
                }),
              ),
            ]
          : []),
      ],
    });
    active.current = { views: [view], wrapping, comparison };
    restore(view);
    onNavigate?.((forward) => {
      (forward ? goToNextChunk : goToPreviousChunk)(view);
    });
    return () => {
      remember(view);
      active.current = undefined;
      view.destroy();
    };
  }, [path, text, before, split, label, beforeLabel, onNavigate]);
  useEffect(() => {
    const current = active.current;
    if (!current) return;
    for (const view of current.views)
      view.dispatch({
        effects: [
          current.wrapping.reconfigure(wrap ? EditorView.lineWrapping : []),
          view.scrollSnapshot(),
        ],
      });
  }, [wrap]);
  useEffect(() => {
    const current = active.current;
    if (!current || before === undefined) return;
    const collapseUnchanged = collapse ? { margin: 3, minSize: 8 } : undefined;
    const snapshots = current.views.map((view) => view.scrollSnapshot());
    if (current.merge) current.merge.reconfigure({ collapseUnchanged });
    else
      current.views[0].dispatch({
        effects: current.comparison.reconfigure(
          unifiedMergeView({
            original: Text.of(sourceLines(before)),
            mergeControls: false,
            highlightChanges: true,
            gutter: true,
            diffConfig: { scanLimit: 10000, timeout: 1000 },
            collapseUnchanged,
          }),
        ),
      });
    current.views.forEach((view, index) =>
      view.dispatch({ effects: snapshots[index] }),
    );
  }, [collapse, before]);
  return <div ref={host} className={styles.editor} />;
}
