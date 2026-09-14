import { useMemo, useState } from "react";
import { Decoration, EditorView, GutterMarker, gutter } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import type { Schema } from "../transport/client";
import { SourceEditor } from "../configuration/editor";
import { CaptureContext } from "./capture";
import type { LineRange } from "./buffer";
import { patchLines, type PatchLine } from "./patch";
import styles from "./native.module.css";

class FileLines extends GutterMarker {
  constructor(readonly line: PatchLine) {
    super();
  }
  eq(other: FileLines) {
    return (
      this.line.oldLine === other.line.oldLine &&
      this.line.newLine === other.line.newLine
    );
  }
  toDOM() {
    const element = document.createElement("span");
    for (const value of [this.line.oldLine, this.line.newLine]) {
      const number = document.createElement("span");
      number.textContent = value?.toString() ?? "";
      element.append(number);
    }
    return element;
  }
}

export function PatchEditor({
  value,
  threadId,
  disabled,
}: {
  value: Schema<"GitDiff">;
  threadId?: string;
  disabled: boolean;
}) {
  const text = value.text ?? "";
  const [range, setRange] = useState<LineRange>();
  const lines = useMemo(() => patchLines(text), [text]);
  const extensions = useMemo(() => {
    const doc = EditorState.create({ doc: text }).doc;
    return [
      gutter({
        class: "cm-diffLineNumbers",
        lineMarker: (view, line) =>
          new FileLines(lines[view.state.doc.lineAt(line.from).number - 1]),
      }),
      EditorView.decorations.of(
        Decoration.set(
          lines.map((line, index) =>
            Decoration.line({ class: `cm-diff-${line.kind}` }).range(
              doc.line(index + 1).from,
            ),
          ),
        ),
      ),
      EditorView.theme({
        // CodeMirror's base gutter layout sets display:flex !important.
        ".cm-gutters .cm-lineNumbers, .cm-gutters .cm-foldGutter": {
          display: "none !important",
        },
        ".cm-content, .cm-gutters": {
          fontFamily: "var(--a13n-mono)",
          lineHeight: "1.65",
        },
        ".cm-line": { paddingInline: "12px" },
        ".cm-diffLineNumbers .cm-gutterElement > span": { display: "flex" },
        ".cm-diffLineNumbers .cm-gutterElement > span > span": {
          minWidth: "4ch",
          textAlign: "right",
          paddingInline: "8px",
          boxSizing: "content-box",
        },
        ".cm-diffLineNumbers .cm-gutterElement > span > span + span": {
          borderLeft: "1px solid var(--a13n-border)",
        },
        ".cm-diff-addition": { backgroundColor: "var(--diff-added)" },
        ".cm-diff-deletion": { backgroundColor: "var(--diff-deleted)" },
        ".cm-diff-hunk": {
          backgroundColor: "var(--diff-hunk)",
          color: "var(--a13n-secondary)",
        },
        ".cm-diff-metadata": { color: "var(--a13n-secondary)" },
        ".cm-activeLine, .cm-activeLineGutter": {
          backgroundColor: "transparent",
        },
      }),
    ];
  }, [lines, text]);
  return (
    <>
      <div className={styles.diffSummary}>
        <span title="Unified diff">
          {value.comparison === "staged"
            ? "HEAD → index"
            : value.comparison === "unstaged"
              ? "Index → worktree"
              : "New file"}
        </span>
        <span className={styles.addedCount}>
          +{lines.filter((line) => line.kind === "addition").length}
        </span>
        <span className={styles.deletedCount}>
          −{lines.filter((line) => line.kind === "deletion").length}
        </span>
        <small>Old / new lines</small>
      </div>
      <div className={`${styles.editor} ${styles.patchEditor}`}>
        <SourceEditor
          value={text}
          language="plain"
          fill
          readOnly
          wrap={false}
          extensions={extensions}
          label="Git patch with old and new file line numbers"
          onSelection={setRange}
        />
      </div>
      {range && (
        <small className={styles.path}>
          Selected patch lines {range.start_line}–{range.end_line} (including
          headers)
        </small>
      )}
      <CaptureContext
        source={{ diff: value }}
        threadId={threadId}
        selection={range}
        disabled={disabled}
      />
    </>
  );
}
