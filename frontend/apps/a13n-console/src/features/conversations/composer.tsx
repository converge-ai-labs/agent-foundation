import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Dialog } from "a13n-ui";
import { Paperclip, ArrowUp, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { TextArea } from "../../shared/form";
import styles from "./conversations.module.css";

export function Composer({
  initial,
  submit,
  label,
  disabled = false,
  children,
}: {
  initial?: Schema["AgentInput"];
  submit: (input: Schema["AgentInput"], key: string) => Promise<unknown>;
  label?: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const [text, setText] = useState(
    initial?.content
      ?.filter((block) => block.type === "text")
      .map((block) => block.text)
      .join("\n\n") ?? "",
  );
  const [attachments, setAttachments] = useState<Schema["BinaryContent"][]>(
    initial?.content?.filter((block) => block.type === "binary") ?? [],
  );
  const [structured, setStructured] = useState(
    initial?.structured_content == null
      ? ""
      : JSON.stringify(initial.structured_content, null, 2),
  );
  const [url, setUrl] = useState(""),
    [key, setKey] = useState(crypto.randomUUID()),
    [uploadFile, setUploadFile] = useState<{ file: File; key: string }>();
  const changed = () => setKey(crypto.randomUUID());
  const upload = useMutation({
    mutationFn: async (selection: { file: File; key: string }) =>
      client.http
        .POST("/api/v1/workspaces/{workspace_id}/assets", {
          params: {
            path: { workspace_id: workspace.id },
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
      setAttachments((previous) => [
        ...previous,
        {
          type: "binary",
          source: { type: "asset", asset_id: asset.id },
          filename: asset.filename,
          media_type: asset.media_type,
        },
      ]);
      setUploadFile(undefined);
      changed();
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
    },
  });
  const busy = mutation.isPending || upload.isPending;
  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <fieldset disabled={disabled || busy} className={styles.composerFields}>
        <textarea
          className={styles.messageInput}
          aria-label={t("Message")}
          placeholder={t("Message your agent…")}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            changed();
            event.target.style.height = "auto";
            event.target.style.height = `${Math.min(event.target.scrollHeight, 240)}px`;
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.metaKey || event.ctrlKey) &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              if (!busy && !disabled) event.currentTarget.form?.requestSubmit();
            }
          }}
          rows={3}
        />
        {!!attachments.length && (
          <div className={styles.attachments}>
            {attachments.map((attachment, index) => (
              <span className={styles.attachment} key={index}>
                <Paperclip size={13} />
                {attachment.filename ||
                  (attachment.source.type === "url"
                    ? attachment.source.url
                    : attachment.source.type === "asset"
                      ? attachment.source.asset_id
                      : attachment.source.path)}
                <button
                  type="button"
                  aria-label={t("Remove attachment")}
                  onClick={() => {
                    setAttachments((previous) =>
                      previous.filter((_, i) => i !== index),
                    );
                    changed();
                  }}
                >
                  <X size={13} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className={styles.composerFooter}>
          <div className={styles.composerTools}>
            <Dialog
              description={t(
                "Add files, a URL, or structured input to your message.",
              )}
              title={t("Attach content")}
              closeLabel={t("Close")}
              trigger={
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={t("Attach content")}
                  icon={<Paperclip size={16} />}
                />
              }
            >
              <fieldset
                disabled={disabled || busy}
                className={`${styles.composerOptions} fieldset-reset`}
              >
                {can("asset.create") && (
                  <label>
                    {t("Upload a file")}
                    <input
                      type="file"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) {
                          const selection = { file, key: crypto.randomUUID() };
                          setUploadFile(selection);
                          upload.mutate(selection);
                        }
                        event.target.value = "";
                      }}
                    />
                  </label>
                )}
                <div className={styles.inline}>
                  <Input
                    label={t("File URL")}
                    value={url}
                    onChange={(event) => setUrl(event.target.value)}
                    placeholder="https://"
                  />
                  <Button
                    type="button"
                    disabled={!/^https?:\/\//i.test(url)}
                    onClick={() => {
                      setAttachments((previous) => [
                        ...previous,
                        { type: "binary", source: { type: "url", url } },
                      ]);
                      setUrl("");
                      changed();
                    }}
                  >
                    {t("Attach URL")}
                  </Button>
                </div>
                <TextArea
                  label={t("Structured input (JSON)")}
                  value={structured}
                  onChange={(value) => {
                    setStructured(value);
                    changed();
                  }}
                  rows={3}
                  code
                />
              </fieldset>
            </Dialog>
            {children}
          </div>
          <div className={styles.composerSend}>
            <span className={styles.shortcut}>{t("⌘ / Ctrl ↵ to send")}</span>
            <Button
              type="submit"
              variant="primary"
              icon={<ArrowUp size={15} />}
              loading={mutation.isPending}
              disabled={
                !text.trim() && !attachments.length && !structured.trim()
              }
            >
              {label ?? t("Send")}
            </Button>
          </div>
        </div>
      </fieldset>
      <ErrorNotice error={mutation.error} />
      <ErrorNotice
        error={upload.error}
        retry={uploadFile ? () => upload.mutate(uploadFile) : undefined}
      />
    </form>
  );
}
