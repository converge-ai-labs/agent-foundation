import { ModalFrame } from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, rowTag, type Schema } from "../../shared/api";
import { ApiError } from "../../service-client";
import { ErrorNotice } from "../../shared/feedback";
import {
  ExecutionFields,
  executionPatch,
  FormActions,
} from "../../shared/forms";
import { useMountedModal, ConflictNotice } from "../../shared/dialogs";
import { conversationKeys } from "./api";

export function ThreadExecutionDialog({
  thread,
  onClose: onClosed,
}: {
  thread: Schema["ThreadView"];
  onClose: () => void;
}) {
  const { close: onClose, modalProps } = useMountedModal(onClosed);
  const { t } = useTranslation();
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [base, setBase] = useState(thread);
  const [execution, setExecution] = useState(thread.execution ?? {});
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/threads/{thread_id}", {
          params: {
            path: { thread_id: base.id },
            header: { "If-Match": rowTag(base) },
          },
          body: { execution: executionPatch(execution) },
        })
        .then(data),
    onSuccess: (value) => {
      const keys = conversationKeys(workspace.id);
      cache.setQueryData(keys.thread(value.id), value);
      onClose();
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id: base.id } },
        })
        .then(data),
    onSuccess: (value) => {
      setBase(value);
      setExecution(value.execution ?? {});
      save.reset();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  const pending = save.isPending || reload.isPending;
  return (
    <ModalFrame
      {...modalProps}
      title={t("Thread execution policy")}
      description={t(
        "Applies to new runs in this thread. Inherited fields follow the selected agent.",
      )}
      closeLabel={t("Close")}
    >
      <form
        className="grid gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset disabled={pending} className="fieldset-reset">
          <ExecutionFields value={execution} onChange={setExecution} />
        </fieldset>
        {conflict ? (
          <ConflictNotice
            title={t("This thread changed")}
            description={t(
              "Reload the saved policy before making further changes.",
            )}
            recover={{
              label: t("Reload saved policy"),
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
