import { Button, Textarea } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { ArrowUpIcon, SquareIcon, XIcon } from "@phosphor-icons/react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { AttachmentChip } from "../transcript/attachment";
import { AttachDialog } from "./attach-dialog";
import styles from "./composer.module.css";

/**
 * The composer floats over the end of the transcript: a borderless text area,
 * the attachment and option affordances at the left, and one round send button
 * whose label always names the action it performs.
 */
export function Composer({
  initial,
  submit,
  label,
  placeholder,
  disabled = false,
  agentName,
  options,
  stop,
  stopping = false,
}: {
  initial?: Schema["AgentInput"];
  submit: (input: Schema["AgentInput"], key: string) => Promise<unknown>;
  /** Names the action: Send, Send guidance, Run next step. */
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  agentName?: string;
  /** Option chips shown beside the attachment control. */
  options?: ReactNode;
  /** Interrupts the active run; the empty-draft send button becomes Stop. */
  stop?: () => void;
  stopping?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [text, setText] = useState(
    initial?.content
      ?.filter((block) => block.type === "text")
      .map((block) => block.text)
      .join("\n\n") ?? "",
  );
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  useLayoutEffect(() => {
    const input = messageInput.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  }, [text]);
  const [attachments, setAttachments] = useState<Schema["BinaryContent"][]>(
    initial?.content?.filter((block) => block.type === "binary") ?? [],
  );
  const [structured, setStructured] = useState(
    initial?.structured_content == null
      ? ""
      : JSON.stringify(initial.structured_content, null, 2),
  );
  const [key, setKey] = useState(crypto.randomUUID()),
    [uploadFile, setUploadFile] = useState<{ file: File; key: string }>();
  const changed = () => setKey(crypto.randomUUID());
  const upload = useMutation({
    mutationFn: async (selection: { file: File; key: string }) =>
      client.http
        .POST("/api/v1/workspaces/{workspace}/assets", {
          params: {
            path: { workspace: workspace.id },
            query: {
              filename: selection.file.name,
              media_type: selection.file.type || "application/octet-stream",
            },
            header: commandHeaders(workspace.id, selection.key),
          },
          headers: { "Content-Type": "application/octet-stream" },
          body: selection.file,
        })
        .then(data),
    onSuccess: (asset) => {
      attach({
        type: "binary",
        source: { type: "asset", asset_id: asset.id },
        filename: asset.filename,
        media_type: asset.media_type,
      });
      setUploadFile(undefined);
    },
  });
  const mutation = useMutation({
    mutationFn: async () => {
      let structuredContent: Schema["JsonValue"] | undefined;
      if (structured.trim()) {
        try {
          structuredContent = JSON.parse(structured);
        } catch {
          throw new Error(t("Structured input must be valid JSON."));
        }
      }
      if (
        !text.trim() &&
        !attachments.length &&
        structuredContent === undefined
      )
        throw new Error(t("Write a message or attach content first."));
      await submit(
        {
          ...initial,
          schema_version: "2",
          content: [
            ...(text.trim() ? [{ type: "text" as const, text }] : []),
            ...attachments,
          ],
          structured_content: structuredContent ?? null,
        },
        key,
      );
    },
    onSuccess: () => {
      setText("");
      setAttachments([]);
      setStructured("");
      changed();
      // Sending always returns the reader to the live end of the transcript.
      const stage = form.current?.closest("[data-session-stage]");
      if (stage instanceof HTMLElement)
        stage.scrollTo({ top: stage.scrollHeight, behavior: "smooth" });
    },
  });
  function attach(attachment: Schema["BinaryContent"]) {
    setAttachments((previous) => [...previous, attachment]);
    changed();
  }
  const busy = mutation.isPending || upload.isPending;
  const action = label ?? t("Send");
  const empty = !text.trim() && !attachments.length && !structured.trim();
  // A run in flight and nothing to say: the one round button stops it instead.
  const stopMode = !!stop && empty;
  return (
    <form
      ref={form}
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <fieldset disabled={disabled || busy} className={styles.fields}>
        <Textarea
          ref={messageInput}
          rows={1}
          unstyled
          className={styles.messageInput}
          aria-label={t("Message")}
          placeholder={
            placeholder ??
            (agentName
              ? t("Message {{agent}}…", { agent: agentName })
              : t("Message your agent…"))
          }
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            changed();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && stopMode) {
              event.preventDefault();
              stop?.();
              return;
            }
            if (
              event.key !== "Enter" ||
              event.shiftKey ||
              event.nativeEvent.isComposing
            )
              return;
            event.preventDefault();
            if (!busy && !disabled) event.currentTarget.form?.requestSubmit();
          }}
        />
        {!!attachments.length && (
          <div className={styles.attachments}>
            {attachments.map((attachment, index) => (
              <AttachmentChip
                key={index}
                label={
                  attachment.filename ||
                  (attachment.source.type === "url"
                    ? attachment.source.url
                    : attachment.source.type === "asset"
                      ? attachment.source.asset_id
                      : attachment.source.path)
                }
                actions={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Remove attachment")}
                    title={t("Remove attachment")}
                    onClick={() => {
                      setAttachments((previous) =>
                        previous.filter((_, position) => position !== index),
                      );
                      changed();
                    }}
                  >
                    <XIcon size={12} />
                  </Button>
                }
              />
            ))}
          </div>
        )}
        <div className={styles.tools}>
          <AttachDialog
            disabled={disabled || busy}
            structured={structured}
            onStructuredChange={(value) => {
              setStructured(value);
              changed();
            }}
            onAttach={attach}
            onUpload={(file) => {
              if (!file) {
                setUploadFile(undefined);
                return;
              }
              const selection = { file, key: crypto.randomUUID() };
              setUploadFile(selection);
              upload.mutate(selection);
            }}
            uploading={upload.isPending}
            uploadedFile={uploadFile?.file}
          />
          {options}
        </div>
      </fieldset>
      <div className={styles.send}>
        <span className={styles.hint}>
          {stopMode ? t("Esc stops") : t("Enter to send")}
        </span>
        {stopMode ? (
          <Button
            type="button"
            size="icon-sm"
            variant="default"
            className={styles.sendButton}
            aria-label={t("Stop")}
            title={t("Stop")}
            loading={stopping}
            onClick={stop}
          >
            <SquareIcon size={12} weight="fill" />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon-sm"
            variant="default"
            className={styles.sendButton}
            aria-label={action}
            title={action}
            disabled={empty || disabled || busy}
            loading={mutation.isPending}
          >
            <ArrowUpIcon size={15} />
          </Button>
        )}
      </div>
      <ErrorNotice error={mutation.error} />
      <ErrorNotice
        error={upload.error}
        retry={uploadFile ? () => upload.mutate(uploadFile) : undefined}
      />
    </form>
  );
}
