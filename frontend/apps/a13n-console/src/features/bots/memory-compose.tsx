import {
  Button,
  ChoiceField,
  FormField,
  Input,
  ModalFrame,
  Textarea,
} from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import { MarkdownContent } from "../../shared/markdown";
import { refreshMemory, unconfirmedWrite } from "./memory-actions";
import { GroupPicker, useMemoryGroups } from "./memory-groups";
import shared from "../../shared/shared.module.css";
import styles from "./bots.module.css";

type Props = {
  account: Schema["Account"];
  scopeId: string;
  source?: Schema["Document"];
  mode: "create" | "correction" | "publish";
  onCreated?: (id: string) => void;
};

export function MemoryComposer(props: Props) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false);
  const label =
    props.mode === "publish"
      ? "Share memory"
      : props.mode === "correction"
        ? "Add correction"
        : "Create memory";
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (!busy) setOpen(value);
      }}
      title={t(label)}
      closeLabel={t("Close")}
      size="lg"
      trigger={
        <Button type="button" variant="outline" size="sm">
          {t(label)}
        </Button>
      }
    >
      {open && (
        <ComposerForm
          {...props}
          setBusy={setBusy}
          close={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function ComposerForm({
  account,
  scopeId,
  source,
  mode,
  onCreated,
  setBusy,
  close,
}: Props & { setBusy: (busy: boolean) => void; close: () => void }) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    key = useIdempotency();
  const [title, setTitle] = useState(
    mode === "publish" ? (source?.title ?? "") : "",
  );
  const [description, setDescription] = useState(
    mode === "publish" ? (source?.description ?? "") : "",
  );
  const [text, setText] = useState(
    mode === "publish" ? (source?.text ?? "") : "",
  );
  const [kind, setKind] = useState<"daily" | "long_term">("long_term");
  const [recipients, setRecipients] = useState<string[]>([]),
    [preview, setPreview] = useState(false),
    [uncertain, setUncertain] = useState(false);
  const groups = useMemoryGroups(account, mode === "publish");
  const mutation = useMutation({
    retry: false,
    onMutate: () => setBusy(true),
    onSettled: () => setBusy(false),
    mutationFn: async () => {
      const path = { account_id: account.id, scope_id: scopeId };
      if (mode === "publish") {
        if (!source || !recipients.length)
          throw new Error(t("Choose at least one recipient group."));
        const body = {
          title,
          description,
          text,
          recipient_scope_ids: recipients,
        };
        return client.http
          .POST(
            "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/{document_id}/publications",
            {
              params: {
                path: { ...path, document_id: source.id },
                header: {
                  "Idempotency-Key": key.forBody({
                    path,
                    source: source.id,
                    body,
                  }),
                },
              },
              body,
            },
          )
          .then(data);
      }
      const body = {
        title,
        description,
        text,
        kind,
        correction_of: mode === "correction" ? source?.id : undefined,
      };
      return client.http
        .POST(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents",
          {
            params: {
              path,
              header: { "Idempotency-Key": key.forBody({ path, body }) },
            },
            body,
          },
        )
        .then(data);
    },
    onError: (error) => {
      if (unconfirmedWrite(error)) setUncertain(true);
    },
    onSuccess: async (document) => {
      await refreshMemory(cache, account.id);
      if (mode !== "publish") onCreated?.(document.id);
      close();
    },
  });
  const locked = mutation.isPending || uncertain;
  return (
    <form
      className={shared.stack}
      onSubmit={(event) => {
        event.preventDefault();
        if (!preview) setPreview(true);
        else if (!uncertain) mutation.mutate();
      }}
    >
      <p>
        {t(
          mode === "publish"
            ? "Review the exact published copy. The source stays unchanged; the copy cannot be edited after publication."
            : "Saved memory is immutable. Corrections create a new document and preserve the original.",
        )}
      </p>
      {mode === "correction" && source && (
        <p>
          {t("Corrects")}: <strong>{source.title}</strong>
        </p>
      )}
      {!preview ? (
        <>
          <FormField label={t("Title")}>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={160}
              required
              disabled={locked}
            />
          </FormField>
          <FormField label={t("Index description")}>
            <Input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={320}
              disabled={locked}
            />
          </FormField>
          {mode !== "publish" && (
            <ChoiceField
              label={t("Kind")}
              value={kind}
              onValueChange={(value) => setKind(value as "daily" | "long_term")}
              options={[
                { value: "long_term", label: t("Long-term") },
                { value: "daily", label: t("Daily") },
              ]}
            />
          )}
          <FormField label={t("Memory content")}>
            <Textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={10}
              maxLength={8000}
              required
              disabled={locked}
            />
          </FormField>
          {mode === "publish" && (
            <GroupPicker
              groups={groups}
              exclude={scopeId}
              value={recipients}
              onChange={setRecipients}
              disabled={locked}
            />
          )}
        </>
      ) : (
        <div className={styles.draftPreview}>
          <h3>{title}</h3>
          <p>{description}</p>
          <MarkdownContent text={text} />
          {mode === "publish" && (
            <>
              <h4>{t("Recipient groups")}</h4>
              <p>
                {recipients
                  .map(
                    (id) =>
                      groups?.data?.find((group) => group.id === id)?.name ??
                      id,
                  )
                  .join(", ")}
              </p>
              <p>
                {t(
                  "No chat message will be sent. Only this approved copy becomes available to the selected groups.",
                )}
              </p>
            </>
          )}
        </div>
      )}
      <ErrorNotice error={mutation.error} />
      {uncertain && (
        <p role="alert">
          {t(
            "The result is unconfirmed. Your draft is preserved. Check Pending operations before creating or publishing another copy.",
          )}
        </p>
      )}
      <div className={styles.memoryActions}>
        <Button
          type="button"
          variant="outline"
          disabled={mutation.isPending}
          onClick={close}
        >
          {t("Close")}
        </Button>
        {preview && (
          <Button
            type="button"
            variant="outline"
            disabled={locked}
            onClick={() => setPreview(false)}
          >
            {t("Back to draft")}
          </Button>
        )}
        <Button
          type="submit"
          disabled={
            locked ||
            !title.trim() ||
            !text.trim() ||
            (mode === "publish" && !recipients.length)
          }
          loading={mutation.isPending}
        >
          {t(
            !preview
              ? "Review"
              : mode === "publish"
                ? "Publish copy"
                : "Save memory",
          )}
        </Button>
      </div>
    </form>
  );
}
