import { FormField, Input, ModalFrame } from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, rowTag, type Schema } from "../../shared/api";
import { ApiError } from "../../service-client";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, FormSection } from "../../shared/forms";
import { useMountedModal, ConflictNotice } from "../../shared/dialogs";
import { conversationKeys } from "./api";
import styles from "./session-list.module.css";

export function SessionEditor({
  session,
  onClose: onClosed,
}: {
  session: Schema["SessionView"];
  onClose: () => void;
}) {
  const { close: onClose, modalProps } = useMountedModal(onClosed);
  const { t } = useTranslation();
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  // Keep the original version while the list/stream refreshes behind this draft.
  const [base, setBase] = useState(session);
  const [title, setTitle] = useState(session.title ?? "");
  const [application, setApplication] = useState(
    session.origin?.application ?? "",
  );
  const [name, setName] = useState(session.origin?.name ?? "");
  const [externalId, setExternalId] = useState(
    session.origin?.external_id ?? "",
  );
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/sessions/{session_id}", {
          params: {
            path: { session_id: base.id },
            header: { "If-Match": rowTag(base) },
          },
          body: {
            title: title.trim() || null,
            origin: application.trim()
              ? {
                  application: application.trim(),
                  name: name.trim() || null,
                  external_id: externalId.trim() || null,
                }
              : null,
          },
        })
        .then(data),
    onSuccess: (value) => {
      const keys = conversationKeys(workspace.id);
      cache.setQueryData(keys.session(value.id), value);
      void cache.invalidateQueries({ queryKey: keys.sessions() });
      onClose();
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/sessions/{session_id}", {
          params: { path: { session_id: base.id } },
        })
        .then(data),
    onSuccess: (value) => {
      setBase(value);
      setTitle(value.title ?? "");
      setApplication(value.origin?.application ?? "");
      setName(value.origin?.name ?? "");
      setExternalId(value.origin?.external_id ?? "");
      save.reset();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  const pending = save.isPending || reload.isPending;
  return (
    <ModalFrame
      {...modalProps}
      title={t("Edit session details")}
      description={t(
        "Give this session a recognizable name and record where it started.",
      )}
      closeLabel={t("Close")}
    >
      <form
        className={styles.editor}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset disabled={pending} className={styles.fields}>
          <FormField label={t("Session title")}>
            <Input
              autoFocus
              value={title}
              maxLength={256}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={t("What is this session about?")}
            />
          </FormField>
          <FormSection title={t("Source")}>
            <FormField
              label={t("Source application")}
              description={t(
                "The application that started this session. Leave empty to remove its origin.",
              )}
            >
              <Input
                value={application}
                maxLength={128}
                onChange={(event) => setApplication(event.target.value)}
                placeholder="a13n-bot"
              />
            </FormField>
            <FormField label={t("Source name")}>
              <Input
                value={name}
                disabled={!application.trim()}
                maxLength={256}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("Repository, channel, or discussion")}
              />
            </FormField>
            <FormField label={t("External ID")}>
              <Input
                value={externalId}
                disabled={!application.trim()}
                maxLength={512}
                onChange={(event) => setExternalId(event.target.value)}
              />
            </FormField>
          </FormSection>
        </fieldset>
        {conflict ? (
          <ConflictNotice
            title={t("This session changed")}
            description={t(
              "Your edits are preserved. Reload the saved details before making further changes.",
            )}
            recover={{
              label: t("Reload saved details"),
              onClick: () => reload.mutate(),
              pending: reload.isPending,
            }}
          />
        ) : (
          <ErrorNotice error={save.error} />
        )}
        <ErrorNotice error={reload.error} />
        <FormActions pending={pending} disabled={conflict} onCancel={onClose} />
      </form>
    </ModalFrame>
  );
}
