import { useEffect, useMemo, useRef } from "react";
import { X } from "@phosphor-icons/react";
import { useAppearance } from "../appearance/react";
import { useTranslation } from "../i18n";
import {
  drawSelection,
  EditorView,
  keymap,
  placeholder,
  tooltips,
} from "@codemirror/view";
import { Compartment, Prec, type Transaction } from "@codemirror/state";
import {
  autocompletion,
  completionKeymap,
  acceptCompletion,
  closeCompletion,
  currentCompletions,
  pickedCompletion,
  startCompletion,
} from "@codemirror/autocomplete";
import {
  skillCompletion,
  skillSourceLabel,
  type SkillOption,
  type LoadSkills,
} from "./skill-references";
import skillStyles from "./skill-chip.module.css";
import { composerSkills, refreshSkills } from "./composer-skills";
import { composerWordKeymap } from "./composer-word-motion";
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

function typedSkillTrigger(tr: Transaction) {
  if (!tr.isUserEvent("input.type")) return false;
  let triggered = false;
  tr.changes.iterChanges((_from, _to, _newFrom, _newTo, inserted) => {
    if (/[$/]/.test(inserted.toString())) triggered = true;
  });
  return triggered;
}

export function ComposerEditor({
  draft,
  profile,
  presence,
  submit,
  attachments,
  editor,
  autoFocus = false,
  local = false,
  loadSkills,
  skillContext,
  draftDescription,
}: {
  draft: ThreadDraft;
  profile: Profile;
  presence: (value: Schema<"DraftPresence">) => void;
  submit: () => void;
  attachments?: ComposerAttachmentView;
  editor?: { current: EditorView | null };
  autoFocus?: boolean;
  local?: boolean;
  loadSkills?: LoadSkills;
  skillContext?: string;
  draftDescription?: string;
}) {
  const { t } = useTranslation();
  const translate = useRef(t);
  translate.current = t;
  const pickerHost = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const attributes = useRef(new Compartment());
  const skills = useRef(new Compartment());
  const load = useRef(loadSkills);
  load.current = loadSkills;
  const currentView = useRef<EditorView | null>(null);
  const description =
    draftDescription ??
    (local
      ? t(
          "Private until you send. Drafts stay in this browser when storage is available. Files upload on Send; reattach them after a reload if needed. Enter to send, Shift+Enter for a new line.",
        )
      : t(
          "Shared draft. Lost if the server restarts. Enter to send, Shift+Enter for a new line.",
        ));
  const contentAttributes = useMemo(
    () => [
      placeholder(t("Ask anything, or describe a task…")),
      EditorView.contentAttributes.of({
        "aria-label": local ? t("Message") : t("Shared prompt"),
        "data-composer-editor": "",
        "aria-multiline": "true",
        "aria-description": description,
        role: "textbox",
      }),
    ],
    [description, local, t],
  );
  const currentAttributes = useRef(contentAttributes);
  currentAttributes.current = contentAttributes;
  const send = useRef(submit);
  const report = useRef(presence);
  const person = useRef(profile);
  const attachmentContext = useRef(attachments);
  attachmentContext.current = attachments;
  send.current = submit;
  report.current = presence;
  person.current = profile;
  const { appearance } = useAppearance();
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
        // Match the caret to the text metrics, including an empty placeholder.
        drawSelection(),
        EditorView.domEventHandlers({
          focus: activate,
          keydown: activate,
          pointerdown: activate,
          input: activate,
          blur: clearCursor,
        }),
        ...(attachments
          ? [
              composerAttachments(
                draft,
                () => attachmentContext.current!,
                () => translate.current,
              ),
            ]
          : []),
        attributes.current.of(currentAttributes.current),
        skills.current.of([]),
        keymap.of([
          ...["Enter", "Mod-Enter"].map((key) => ({
            key,
            run: (editor: EditorView) => {
              // Only visible candidates may consume Enter instead of sending.
              if (
                key === "Enter" &&
                currentCompletions(editor.state).length > 0
              )
                return true;
              if (!editor.compositionStarted) send.current();
              return true;
            },
          })),
          { key: "Shift-Enter", run: insertNewline },
          ...yUndoManagerKeymap,
          ...composerWordKeymap,
          ...defaultKeymap,
        ]),
        yCollab(doc.getText("text"), awareness, { undoManager: draft.undo }),
        EditorView.theme({
          "&": {
            backgroundColor: "transparent",
            color: "var(--a13n-text)",
            fontSize: "var(--composer-font-size, 16px)",
            minHeight: "var(--composer-content-height, 28px)",
          },
          "@media (max-width: 640px) and (pointer: coarse)": {
            "&": { fontSize: "max(16px, var(--composer-font-size, 16px))" },
          },
          ".cm-content": {
            fontFamily: "inherit",
            minHeight: "var(--composer-content-height, 28px)",
            padding: "4px var(--composer-text-inset, 0px)",
            lineHeight: "1.5",
          },
          ".cm-line": { padding: "0" },
          ".cm-scroller": {
            maxHeight:
              "max(48px, min(280px, calc(var(--visible-height, 100dvh) * 0.35), calc(var(--visible-height, 100dvh) - 300px)))",
            overflow: "auto",
            fontFamily: "inherit",
          },
          ".cm-placeholder": { color: "var(--harness-placeholder)" },
          ".cm-cursor": { borderLeftColor: "var(--a13n-text)" },
          ".cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground":
            {
              backgroundColor:
                "color-mix(in srgb, var(--a13n-signal) 22%, transparent)",
            },
          "&.cm-focused": { outline: "none" },
          ".cm-ySelectionCaret": {
            borderLeftWidth: "2px",
            borderRightWidth: "1px",
            pointerEvents: "none",
          },
          ".cm-ySelectionCaretDot": { display: "none" },
          ".cm-ySelectionInfo": {
            fontFamily: "inherit",
            fontSize: "var(--a13n-text-body)",
            fontWeight: "500",
            lineHeight: "20px",
            top: "-20px",
            padding: "0 4px",
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
    view.scrollDOM.classList.add("a13n-scrollbar");
    currentView.current = view;
    if (editor) editor.current = view;
    window.addEventListener("blur", clearCursor);
    document.addEventListener("visibilitychange", clearCursor);
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
    currentView.current?.requestMeasure();
  }, [appearance.sizes.composer]);
  useEffect(() => {
    if (autoFocus) currentView.current?.focus();
  }, [autoFocus, draft, doc, editor]);
  useEffect(() => {
    currentView.current?.dispatch({
      effects: [
        attributes.current.reconfigure(contentAttributes),
        refreshAttachments.of(null),
      ],
    });
  }, [contentAttributes]);
  useEffect(() => {
    editor?.current?.dispatch({ effects: refreshAttachments.of(null) });
  }, [attachments, editor]);
  useEffect(() => {
    const view = currentView.current;
    if (!view) return;
    closeCompletion(view);
    let disposed = false;
    let loaded = false;
    let pending: ReturnType<LoadSkills> | undefined;
    const loadCatalog: LoadSkills = () =>
      (pending ??= load.current!()
        .then((catalog) => {
          if (!disposed) {
            loaded = true;
            view.dispatch({ effects: refreshSkills.of(catalog) });
          }
          return catalog;
        })
        .finally(() => {
          pending = undefined;
        }));
    const loadIfNeeded = () => {
      if (!loaded && view.state.doc.toString().includes("$"))
        void loadCatalog().catch(() => {
          /* Ordinary editing remains available. */
        });
    };
    view.dispatch({
      effects: skills.current.reconfigure(
        loadSkills
          ? [
              composerSkills,
              EditorView.updateListener.of((update) => {
                if (
                  update.transactions.some((tr) =>
                    tr.annotation(pickedCompletion),
                  )
                ) {
                  draft.undo.stopCapturing();
                }
                if (update.docChanged) loadIfNeeded();
                if (update.transactions.some(typedSkillTrigger)) {
                  // CodeMirror can open completion once this input update finishes.
                  queueMicrotask(() => {
                    if (!disposed) startCompletion(view);
                  });
                }
              }),
              tooltips({ parent: pickerHost.current!, position: "absolute" }),
              autocompletion({
                override: [skillCompletion(loadCatalog)],
                activateOnTyping: false,
                defaultKeymap: false,
                icons: false,
                aboveCursor: true,
                tooltipClass: () => skillStyles.picker,
                optionClass: () => skillStyles.option,
                addToOptions: [
                  {
                    position: 90,
                    render: (option) => {
                      const label = skillSourceLabel(
                        (option as SkillOption).source,
                      );
                      if (!label) return null;
                      const source = document.createElement("span");
                      source.className = skillStyles.source;
                      source.textContent = t(label);
                      return source;
                    },
                  },
                ],
              }),
              Prec.highest(
                keymap.of([
                  ...completionKeymap,
                  { key: "Tab", run: acceptCompletion },
                ]),
              ),
            ]
          : [],
      ),
    });
    if (loadSkills) {
      view.dispatch({ effects: refreshSkills.of(undefined) });
      loadIfNeeded();
    }
    return () => {
      disposed = true;
    };
  }, [draft, doc, editor, skillContext, !!loadSkills, t]);
  useEffect(() => {
    report.current({ name: profile.display_name, color: profile.color });
  }, [profile]);
  return (
    <>
      <div className={skillStyles.anchor}>
        <div className={skillStyles.pickerHeader}>
          <span>{t("Skills")}</span>
          <button
            type="button"
            aria-label={t("Dismiss skill suggestions")}
            aria-keyshortcuts="Escape"
            title={t("Dismiss skill suggestions (Esc)")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              const view = currentView.current;
              if (view) {
                closeCompletion(view);
                view.focus();
              }
            }}
          >
            <X size={14} aria-hidden />
          </button>
        </div>
        <div ref={pickerHost} />
      </div>
      <div ref={host} data-presence-anchor="composer" />
    </>
  );
}
