import {
  EditorState,
  StateEffect,
  Transaction,
  type Extension,
} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";
import type { Schema, Transport } from "../transport/client";
import type { ThreadDraft } from "./draft";
import { inlinePattern, isReadyAttachment } from "./inline-attachments";
import { retainedImage } from "./attachment-thumbnail";
import styles from "./conversation.module.css";

export type ComposerAttachmentView = {
  transport: Transport;
  threadId: string;
  metadata: Map<string, Schema<"ThreadAttachment">>;
  preview: (id: string, attachment?: Schema<"ThreadAttachment">) => void;
  upload: (files: File[], at?: number) => void;
  retry: (key: string) => void;
};
export const refreshAttachments = StateEffect.define<null>();
const clipboardType = "application/x-a13n-composer";
// CodeMirror can give an equal new widget the old DOM without calling toDOM.
const imageDisposers = new WeakMap<HTMLElement, () => void>();

class AttachmentWidget extends WidgetType {
  constructor(
    readonly key: string,
    readonly id: string | undefined,
    readonly name: string,
    readonly draft: ThreadDraft,
    readonly context: ComposerAttachmentView,
  ) {
    super();
  }
  eq(other: AttachmentWidget) {
    return (
      this.key === other.key &&
      this.id === other.id &&
      this.name === other.name &&
      this.context.transport === other.context.transport &&
      this.context.threadId === other.context.threadId &&
      this.context.metadata.get(this.id ?? "") ===
        other.context.metadata.get(other.id ?? "")
    );
  }
  toDOM(view: EditorView) {
    const chip = document.createElement("span");
    chip.className = styles.inlineAttachment;
    chip.contentEditable = "false";
    const open = document.createElement("button");
    open.type = "button";
    open.className = styles.inlineAttachmentOpen;
    const attachment = this.id ? this.context.metadata.get(this.id) : undefined;
    if (attachment?.media_type.startsWith("image/")) {
      const image = document.createElement("img");
      imageDisposers.set(
        chip,
        retainedImage(
          image,
          this.context.transport,
          this.context.threadId,
          attachment,
        ),
      );
      open.append(image);
    }
    const label = document.createElement("span");
    label.textContent = this.name;
    open.append(label);
    open.title = this.name;
    open.setAttribute("aria-label", this.name);
    open.onclick = () => {
      if (isReadyAttachment(this.id)) this.context.preview(this.id, attachment);
      else this.context.retry(this.key);
    };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = styles.inlineAttachmentRemove;
    remove.setAttribute("aria-label", `Remove ${this.name}`);
    remove.textContent = "×";
    remove.onclick = () =>
      this.draft.removeAttachment(this.key, view.posAtDOM(chip));
    chip.append(open, remove);
    return chip;
  }
  destroy(dom: HTMLElement) {
    imageDisposers.get(dom)?.();
    imageDisposers.delete(dom);
  }
  ignoreEvent() {
    return true;
  }
}

export function composerAttachments(
  draft: ThreadDraft,
  context: () => ComposerAttachmentView,
): Extension {
  const label = (key: string) => {
    const id = draft.doc.getMap<string>("attachments").get(key);
    return isReadyAttachment(id)
      ? (context().metadata.get(id)?.name ?? "Attachment")
      : `${draft.uploads.get(key)?.file.name ?? "Attachment"} · ${id === "failed" ? "upload failed — retry" : "not ready"}`;
  };
  const displayText = (text: string) =>
    text.replace(inlinePattern, (_match, key: string) => `[${label(key)}]`);
  const decorations = (view: EditorView) =>
    Decoration.set(
      [...view.state.doc.toString().matchAll(inlinePattern)].map((match) =>
        Decoration.replace({
          widget: new AttachmentWidget(
            match[1],
            draft.doc.getMap<string>("attachments").get(match[1]),
            label(match[1]),
            draft,
            context(),
          ),
        }).range(match.index, match.index + match[0].length),
      ),
    );
  const plugin = ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = decorations(view);
      }
      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.transactions.some((tr) =>
            tr.effects.some((effect) => effect.is(refreshAttachments)),
          )
        )
          this.decorations = decorations(update.view);
      }
    },
    {
      decorations: (value) => value.decorations,
      provide: (value) =>
        EditorView.atomicRanges.of(
          (view) => view.plugin(value)?.decorations ?? Decoration.none,
        ),
    },
  );
  function copy(event: ClipboardEvent, view: EditorView, cut: boolean) {
    const range = view.state.selection.main;
    if (range.empty || !event.clipboardData) return false;
    let { from, to } = range;
    for (const token of view.state.doc.toString().matchAll(inlinePattern)) {
      if (from < token.index + token[0].length && to > token.index) {
        from = Math.min(from, token.index);
        to = Math.max(to, token.index + token[0].length);
      }
    }
    const text = view.state.sliceDoc(from, to);
    event.clipboardData.setData("text/plain", displayText(text));
    event.clipboardData.setData(
      clipboardType,
      JSON.stringify({ draft: draft.draftId, text }),
    );
    event.preventDefault();
    if (cut) view.dispatch({ changes: { from, to }, userEvent: "delete.cut" });
    return true;
  }
  return [
    plugin,
    EditorView.clipboardOutputFilter.of(displayText),
    EditorView.clipboardInputFilter.of((text) => text.replaceAll("\ufffc", "")),
    EditorState.transactionFilter.of((tr) => {
      // Atomic cursor ranges do not protect a programmatically partial selection.
      // Expand local deletions/replacements to the complete attachment identity.
      if (
        !tr.docChanged ||
        (!tr.isUserEvent("delete") && !tr.isUserEvent("input"))
      )
        return tr;
      const tokens = [...tr.startState.doc.toString().matchAll(inlinePattern)];
      const changes: { from: number; to: number; insert: string }[] = [];
      let expanded = false;
      tr.changes.iterChanges((from, to, _fromB, _toB, insert) => {
        for (const token of tokens) {
          const end = token.index + token[0].length;
          if (from < end && to > token.index) {
            if (from > token.index || to < end) expanded = true;
            from = Math.min(from, token.index);
            to = Math.max(to, end);
          }
        }
        changes.push({ from, to, insert: insert.toString() });
      });
      return expanded
        ? {
            changes,
            userEvent: tr.annotation(Transaction.userEvent),
            scrollIntoView: tr.scrollIntoView,
          }
        : tr;
    }),
    EditorView.domEventHandlers({
      copy: (event, view) => copy(event, view, false),
      cut: (event, view) => copy(event, view, true),
      paste: (event, view) => {
        const files = Array.from(event.clipboardData?.files ?? []);
        if (files.length) {
          event.preventDefault();
          context().upload(files, view.state.selection.main.head);
          return true;
        }
        const internal = event.clipboardData?.getData(clipboardType);
        if (internal) {
          try {
            const value: unknown = JSON.parse(internal);
            if (
              typeof value === "object" &&
              value !== null &&
              "draft" in value &&
              value.draft === draft.draftId &&
              "text" in value &&
              typeof value.text === "string" &&
              [...value.text.matchAll(inlinePattern)].every((match) =>
                draft.doc.getMap("attachments").has(match[1]),
              )
            ) {
              event.preventDefault();
              view.dispatch(view.state.replaceSelection(value.text), {
                userEvent: "input.paste",
                scrollIntoView: true,
              });
              return true;
            }
          } catch {
            /* Fall back to ordinary clipboard text. */
          }
        }
        return false;
      },
      drop: (event, view) => {
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (!files.length) return false;
        event.preventDefault();
        context().upload(
          files,
          view.posAtCoords({ x: event.clientX, y: event.clientY }) ??
            view.state.selection.main.head,
        );
        return true;
      },
      dragover: (event) => {
        if (!event.dataTransfer?.types.includes("Files")) return false;
        event.preventDefault();
        return true;
      },
    }),
  ];
}
