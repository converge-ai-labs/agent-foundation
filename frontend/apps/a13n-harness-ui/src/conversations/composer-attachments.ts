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
import chipRemove from "./chip-remove.module.css";
import type { TFunction } from "i18next";

export type ComposerAttachmentView = {
  transport: Transport;
  threadId: string;
  metadata: Map<string, Schema<"ThreadAttachment">>;
  preview: (id: string, attachment?: Schema<"ThreadAttachment">) => void;
  previewLocal: (file: File) => void;
  upload: (files: File[], from: number, to: number) => void;
  retry: (key: string) => void;
};
export const refreshAttachments = StateEffect.define<null>();
const clipboardType = "application/x-a13n-composer";
// CodeMirror can give an equal new widget the old DOM without calling toDOM.
const imageDisposers = new WeakMap<HTMLElement, () => void>();

class AttachmentWidget extends WidgetType {
  readonly file: File | undefined;
  constructor(
    readonly key: string,
    readonly id: string | undefined,
    readonly name: string,
    readonly draft: ThreadDraft,
    readonly context: ComposerAttachmentView,
    readonly removeLabel: string,
    readonly title: string,
    readonly selected: boolean,
  ) {
    super();
    this.file = draft.uploads.get(key)?.file;
  }
  eq(other: AttachmentWidget) {
    return (
      this.sameAttachment(other) &&
      this.name === other.name &&
      this.removeLabel === other.removeLabel &&
      this.title === other.title &&
      this.selected === other.selected
    );
  }
  sameAttachment(other: AttachmentWidget) {
    return (
      this.key === other.key &&
      this.id === other.id &&
      this.file === other.file &&
      this.context.transport === other.context.transport &&
      this.context.threadId === other.context.threadId &&
      this.context.metadata.get(this.id ?? "") ===
        other.context.metadata.get(other.id ?? "")
    );
  }
  updateDOM(dom: HTMLElement, _view: EditorView, previous: AttachmentWidget) {
    if (!this.sameAttachment(previous)) return false;
    this.updatePresentation(dom);
    return true;
  }
  private updatePresentation(dom: HTMLElement) {
    const [open, remove] = dom.querySelectorAll("button");
    const upload = this.draft.uploads.get(this.key);
    open.querySelector("span")!.textContent =
      upload?.status === "staged" ? upload.file.name : this.name;
    open.title = this.title;
    open.setAttribute("aria-label", this.name);
    remove.setAttribute("aria-label", this.removeLabel);
    dom.toggleAttribute("data-selected", this.selected);
    dom.toggleAttribute(
      "data-preview",
      this.id !== "failed" && !!open.querySelector("img"),
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
      image.draggable = false;
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
    } else {
      const file = this.draft.uploads.get(this.key)?.file;
      if (file?.type.startsWith("image/")) {
        const image = document.createElement("img");
        image.draggable = false;
        const url = URL.createObjectURL(file);
        image.onerror = () => {
          image.dataset.failed = "";
          image.hidden = true;
        };
        image.src = url;
        image.alt = "";
        imageDisposers.set(chip, () => URL.revokeObjectURL(url));
        open.append(image);
      }
    }
    open.append(document.createElement("span"));
    open.onclick = () => {
      if (isReadyAttachment(this.id)) this.context.preview(this.id, attachment);
      else {
        const upload = this.draft.uploads.get(this.key);
        if (
          upload?.file.type.startsWith("image/") &&
          upload.status !== "failed"
        )
          this.context.previewLocal(upload.file);
        else this.context.retry(this.key);
      }
    };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = styles.inlineAttachmentRemove;
    const cross = document.createElement("span");
    cross.className = chipRemove.face;
    cross.setAttribute("aria-hidden", "true");
    remove.append(cross);
    remove.onclick = () => {
      const at = view.posAtDOM(chip);
      this.draft.removeAttachment(this.key, at);
      view.dispatch({ selection: { anchor: at } });
      view.focus();
    };
    chip.append(open, remove);
    this.updatePresentation(chip);
    return chip;
  }
  coordsAt(dom: HTMLElement, pos: number) {
    const bounds = dom.getBoundingClientRect();
    const after = pos > 0;
    const rtl = getComputedStyle(dom).direction === "rtl";
    const x = after !== rtl ? bounds.right + 1 : bounds.left - 1;
    const height = Math.min(
      bounds.height,
      parseFloat(getComputedStyle(dom.parentElement!).fontSize) * 1.2,
    );
    const top = bounds.top + (bounds.height - height) / 2;
    return { left: x, right: x, top, bottom: top + height };
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
  translate: () => TFunction,
): Extension {
  const label = (key: string) => {
    const t = translate();
    const id = draft.doc.getMap<string>("attachments").get(key);
    const name = draft.uploads.get(key)?.file.name ?? t("Attachment");
    return isReadyAttachment(id)
      ? (context().metadata.get(id)?.name ?? t("Attachment"))
      : id === "failed"
        ? t("{{name}} - upload failed — retry", { name })
        : draft.uploads.get(key)?.status === "staged"
          ? t("{{name}} - ready to upload", { name })
          : t("{{name}} - not ready", { name });
  };
  const displayText = (text: string) =>
    text.replace(inlinePattern, (_match, key: string) => `[${label(key)}]`);
  const decorations = (view: EditorView) => {
    const t = translate();
    const text = view.state.doc.toString();
    const tokens = [...text.matchAll(inlinePattern)];
    const ranges = tokens.map((match) =>
      Decoration.replace({
        widget: new AttachmentWidget(
          match[1],
          draft.doc.getMap<string>("attachments").get(match[1]),
          label(match[1]),
          draft,
          context(),
          t("Remove {{name}}", { name: label(match[1]) }),
          draft.uploads.get(match[1])?.status === "staged"
            ? t("{{name}} - Uploads when you send", {
                name: draft.uploads.get(match[1])!.file.name,
              })
            : label(match[1]),
          view.state.selection.ranges.some(
            (range) =>
              !range.empty &&
              range.from < match.index + match[0].length &&
              range.to > match.index,
          ),
        ),
      }).range(match.index, match.index + match[0].length),
    );
    return Decoration.set(ranges, true);
  };
  const plugin = ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = decorations(view);
      }
      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          (update.selectionSet &&
            (!update.startState.selection.main.empty ||
              !update.state.selection.main.empty)) ||
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
      JSON.stringify({ draft: draft.draftId ?? draft.doc.guid, text }),
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
          const { from, to } = view.state.selection.main;
          context().upload(files, from, to);
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
              value.draft === (draft.draftId ?? draft.doc.guid) &&
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
        const at =
          view.posAtCoords({ x: event.clientX, y: event.clientY }) ??
          view.state.selection.main.head;
        context().upload(files, at, at);
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
