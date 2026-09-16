import { useEffect, useMemo, useRef } from "react";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { Compartment } from "@codemirror/state";
import { defaultKeymap, insertNewline } from "@codemirror/commands";
import { yCollab, yUndoManagerKeymap } from "y-codemirror.next";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { decode, encode, type ThreadDraft } from "./draft";
import type { Schema } from "../transport/client";
import type { Profile } from "../shell/presence";
import {
  composerAttachments,
  refreshAttachments,
  type ComposerAttachmentView,
} from "./composer-attachments";

export function ComposerEditor({
  draft,
  profile,
  presence,
  submit,
  attachments,
  editor,
  autoFocus = false,
  local = false,
}: {
  draft: ThreadDraft;
  profile: Profile;
  presence: (value: Schema<"DraftPresence">) => void;
  submit: () => void;
  attachments?: ComposerAttachmentView;
  editor?: { current: EditorView | null };
  autoFocus?: boolean;
  local?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const attributes = useRef(new Compartment());
  const currentView = useRef<EditorView | null>(null);
  const description = local
    ? "Private to this tab until you send. Files upload on Send. Reloading discards this draft. Enter to send; Shift+Enter for a new line."
    : "Shared with this conversation. Drafts do not survive server restarts. Enter to send; Shift+Enter for a new line.";
  const contentAttributes = useMemo(
    () =>
      EditorView.contentAttributes.of({
        "aria-label": "Shared prompt",
        "aria-multiline": "true",
        "aria-description": description,
        role: "textbox",
      }),
    [description],
  );
  const initialAttributes = useRef(contentAttributes);
  const send = useRef(submit);
  const report = useRef(presence);
  const person = useRef(profile);
  const attachmentContext = useRef(attachments);
  attachmentContext.current = attachments;
  send.current = submit;
  report.current = presence;
  person.current = profile;
  const doc = draft.doc;
  useEffect(() => {
    if (!host.current) return;
    const awareness = new Awareness(doc);
    let active = false;
    let disposed = false;
    let idle: ReturnType<typeof setTimeout> | undefined;
    const clearCursor = () => {
      active = false;
      clearTimeout(idle);
      awareness.setLocalStateField("cursor", null);
    };
    const activate = () => {
      active = true;
      clearTimeout(idle);
      idle = setTimeout(clearCursor, 30000);
      queueMicrotask(() => {
        if (!disposed && active) view.dispatch({});
      });
    };
    const publish = (_change: unknown, origin: unknown) => {
      if (origin === "server") return;
      const cursor =
        active && document.hasFocus() && document.visibilityState === "visible"
          ? awareness.getLocalState()?.cursor
          : null;
      report.current({
        name: person.current.display_name,
        color: person.current.color,
        anchor: cursor?.anchor
          ? encode(Y.encodeRelativePosition(cursor.anchor))
          : null,
        head: cursor?.head
          ? encode(Y.encodeRelativePosition(cursor.head))
          : null,
      });
    };
    awareness.on("update", publish);
    const syncPresence = () => {
      // These local display keys are not Yjs client IDs or App participant IDs.
      const states = awareness.getStates();
      const removed = [...states.keys()].filter((id) => id !== doc.clientID);
      removed.forEach((id) => states.delete(id));
      const added: number[] = [];
      let key = -1;
      for (const [id, participant] of Object.entries(draft.participants)) {
        if (id === draft.participantId) continue;
        let cursor = null;
        try {
          if (participant.anchor && participant.head)
            cursor = {
              anchor: Y.decodeRelativePosition(decode(participant.anchor)),
              head: Y.decodeRelativePosition(decode(participant.head)),
            };
        } catch {
          /* An opaque position may not belong to this document. */
        }
        states.set(key, {
          user: {
            name: participant.name || "Anonymous",
            color: participant.color,
          },
          cursor,
        });
        added.push(key--);
      }
      awareness.emit("change", [{ added, updated: [], removed }, "server"]);
    };
    const view = new EditorView({
      parent: host.current,
      doc: doc.getText("text").toString(),
      extensions: [
        EditorView.lineWrapping,
        EditorView.domEventHandlers({
          focus: activate,
          keydown: activate,
          pointerdown: activate,
          input: activate,
          blur: clearCursor,
        }),
        ...(attachments
          ? [composerAttachments(draft, () => attachmentContext.current!)]
          : []),
        attributes.current.of(initialAttributes.current),
        placeholder("What would you like to work on?"),
        keymap.of([
          ...["Enter", "Mod-Enter"].map((key) => ({
            key,
            run: (editor: EditorView) => {
              if (!editor.compositionStarted) send.current();
              return true;
            },
          })),
          { key: "Shift-Enter", run: insertNewline },
          ...yUndoManagerKeymap,
          ...defaultKeymap,
        ]),
        yCollab(doc.getText("text"), awareness, { undoManager: draft.undo }),
        EditorView.theme({
          "&": {
            backgroundColor: "transparent",
            color: "var(--a13n-text)",
            fontSize: "var(--composer-font-size, 14px)",
            height: "var(--composer-editor-height, 88px)",
          },
          ".cm-content": {
            fontFamily: "inherit",
            minHeight: "var(--composer-content-height, 64px)",
            padding: "8px 2px",
            lineHeight: "1.6",
          },
          ".cm-scroller": {
            height: "100%",
            overflow: "auto",
            fontFamily: "inherit",
          },
          ".cm-cursor": { borderLeftColor: "var(--a13n-text)" },
          "&.cm-focused": { outline: "none" },
          ".cm-ySelectionCaret": {
            borderLeftWidth: "2px",
            borderRightWidth: "1px",
            pointerEvents: "none",
          },
          ".cm-ySelectionCaretDot": { display: "none" },
          ".cm-ySelectionInfo": {
            fontFamily: "inherit",
            fontSize: "11px",
            fontWeight: "500",
            lineHeight: "18px",
            top: "-20px",
            padding: "0 5px",
            borderRadius: "4px 4px 4px 0",
            maxWidth: "160px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            opacity: "1",
            // Keep arbitrary profile colors identifiable with readable label text.
            color: "var(--a13n-text)",
            backgroundColor: "var(--a13n-canvas)",
            boxShadow: "0 1px 3px #0002",
            borderTop: "2px solid",
            borderTopColor: "inherit",
          },
        }),
      ],
    });
    currentView.current = view;
    if (editor) editor.current = view;
    window.addEventListener("blur", clearCursor);
    document.addEventListener("visibilitychange", clearCursor);
    if (autoFocus) view.focus();
    let scheduled = false;
    const unsubscribe = draft.subscribe(() => {
      if (scheduled) return;
      scheduled = true;
      queueMicrotask(() => {
        scheduled = false;
        if (!disposed) {
          syncPresence();
          view.dispatch({ effects: refreshAttachments.of(null) });
        }
      });
    });
    syncPresence();
    return () => {
      disposed = true;
      clearTimeout(idle);
      unsubscribe();
      window.removeEventListener("blur", clearCursor);
      document.removeEventListener("visibilitychange", clearCursor);
      awareness.off("update", publish);
      currentView.current = null;
      if (editor) editor.current = null;
      view.destroy();
      awareness.destroy();
      report.current({
        name: person.current.display_name,
        color: person.current.color,
        anchor: null,
        head: null,
      });
    };
  }, [draft, doc, editor]);
  useEffect(() => {
    currentView.current?.dispatch({
      effects: attributes.current.reconfigure(contentAttributes),
    });
  }, [contentAttributes]);
  useEffect(() => {
    editor?.current?.dispatch({ effects: refreshAttachments.of(null) });
  }, [attachments, editor]);
  useEffect(() => {
    report.current({ name: profile.display_name, color: profile.color });
  }, [profile]);
  return <div ref={host} data-presence-anchor="composer" />;
}
