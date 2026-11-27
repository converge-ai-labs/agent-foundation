import { useLayoutEffect, useRef } from "react";
import { Button } from "a13n-ui";
import { Check, X } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { ConnectionNoticeContext } from "../shell/connection";
import {
  EmptyResponseError,
  isConnectionError,
  type Schema,
} from "../transport/client";
import type { OrderedInputPart } from "./inline-attachments";
import { useComposerSendShortcut } from "./send-shortcut";
import styles from "./conversation.module.css";

type EditAttachment = {
  attachment: Schema<"ThreadAttachment">;
  offset: number;
  id: string;
};

export type InlineEdit = {
  identity: string;
  selection: Schema<"MessageEditSelection">;
  text: string;
  attachments: EditAttachment[];
  error?: unknown;
};

export type SubmitMessageEdit = (
  selection: Schema<"MessageEditSelection">,
  parts: OrderedInputPart[],
  attachments: Map<string, Schema<"ThreadAttachment">>,
) => Promise<void>;

function replaceEditText(edit: InlineEdit, text: string): InlineEdit {
  // Keep each attachment anchored between its authored text slices. Text edits
  // shift later anchors; replacing a range moves anchors inside it to its end.
  let prefix = 0;
  const shared = Math.min(text.length, edit.text.length);
  while (prefix < shared && text[prefix] === edit.text[prefix]) prefix++;
  let suffix = 0;
  while (
    suffix < shared - prefix &&
    text[text.length - 1 - suffix] === edit.text[edit.text.length - 1 - suffix]
  )
    suffix++;
  return {
    ...edit,
    text,
    attachments: edit.attachments.map((item) => ({
      ...item,
      offset:
        item.offset < prefix
          ? item.offset
          : item.offset >= edit.text.length - suffix
            ? item.offset + text.length - edit.text.length
            : text.length - suffix,
    })),
  };
}

function orderedEditParts(edit: InlineEdit): OrderedInputPart[] {
  const result: OrderedInputPart[] = [];
  let offset = 0;
  for (const item of edit.attachments) {
    if (item.offset > offset) result.push(edit.text.slice(offset, item.offset));
    result.push({ attachment_id: item.attachment.attachment_id });
    offset = item.offset;
  }
  if (offset < edit.text.length) result.push(edit.text.slice(offset));
  return result;
}

function focusEnd(editor: HTMLTextAreaElement | null) {
  if (!editor) return;
  editor.focus();
  editor.setSelectionRange(editor.value.length, editor.value.length);
}

export function MessageEditEditor({
  draft: edit,
  setDraft,
  active,
  blocked,
  pending,
  submit,
}: {
  draft: InlineEdit;
  setDraft: (edit: InlineEdit | null) => void;
  active: boolean;
  blocked: boolean;
  pending: boolean;
  submit: SubmitMessageEdit;
}) {
  const { t } = useTranslation();
  const editor = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    // Complete focus after the transcript and its action button finish their
    // layout transition; a recovery editor can replace this node in that cut.
    const frame = requestAnimationFrame(() => focusEnd(editor.current));
    return () => cancelAnimationFrame(frame);
  }, []);
  const sending = useRef(false);
  const shortcut = useComposerSendShortcut();
  const canSubmit =
    !pending &&
    !blocked &&
    Boolean(edit.text.trim() || edit.attachments.length);
  const label = active ? t("Stop & send") : t("Send");
  const send = async () => {
    if (!canSubmit || sending.current) return;
    sending.current = true;
    setDraft({ ...edit, error: undefined });
    try {
      await submit(
        edit.selection,
        orderedEditParts(edit),
        new Map(
          edit.attachments.map(({ attachment }) => [
            attachment.attachment_id,
            attachment,
          ]),
        ),
      );
      setDraft(null);
    } catch (error) {
      setDraft({ ...edit, error });
    } finally {
      sending.current = false;
    }
  };
  return (
    <form
      className={styles.messageEditor}
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <textarea
        ref={editor}
        aria-label={t("Edit message")}
        aria-keyshortcuts={shortcut.ariaKeyShortcuts}
        rows={1}
        value={edit.text}
        readOnly={pending}
        onChange={(event) =>
          setDraft(replaceEditText(edit, event.target.value))
        }
        onKeyDown={(event) => {
          if (
            event.nativeEvent.isComposing ||
            event.nativeEvent.keyCode === 229
          )
            return;
          if (event.key === "Escape" && !pending) {
            event.preventDefault();
            event.stopPropagation();
            setDraft(null);
          } else if (
            event.key === "Enter" &&
            !event.altKey &&
            !event.shiftKey &&
            ((shortcut.command
              ? event.metaKey && !event.ctrlKey
              : event.ctrlKey && !event.metaKey) ||
              (shortcut.plainEnterSends && !event.ctrlKey && !event.metaKey))
          ) {
            event.preventDefault();
            void send();
          }
        }}
      />
      {edit.attachments.length > 0 && (
        <div className={styles.editAttachments}>
          {edit.attachments.map((item) => (
            <span key={item.id} className={styles.editAttachment}>
              <span>{item.attachment.name}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={pending}
                aria-label={t("Remove {{name}}", {
                  name: item.attachment.name,
                })}
                onClick={() =>
                  setDraft({
                    ...edit,
                    attachments: edit.attachments.filter(
                      ({ id }) => id !== item.id,
                    ),
                  })
                }
              >
                <X aria-hidden="true" />
              </Button>
            </span>
          ))}
        </div>
      )}
      <ConnectionNoticeContext value={false}>
        <ErrorNotice
          inline
          error={edit.error}
          message={
            isConnectionError(edit.error) ||
            edit.error instanceof EmptyResponseError
              ? t(
                  "Could not confirm the edit. Check the conversation before sending again.",
                )
              : undefined
          }
        />
      </ConnectionNoticeContext>
      <div className={styles.editActions}>
        {pending && <small role="status">{t("Sending edit…")}</small>}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title={t("Cancel")}
          aria-label={t("Cancel")}
          disabled={pending}
          onClick={() => setDraft(null)}
        >
          <X aria-hidden="true" />
        </Button>
        <Button
          type="submit"
          size="icon-sm"
          title={label}
          aria-label={label}
          aria-keyshortcuts={shortcut.ariaKeyShortcuts}
          disabled={!canSubmit}
        >
          <Check aria-hidden="true" />
        </Button>
      </div>
    </form>
  );
}
