import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError, ProtocolError, type Client } from "../../service-client";
import { data, ifMatch, representation, type Schema } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/forms";
import { SessionPolicyFields } from "../credentials/session-policy-fields";
import {
  normalizeSessionPolicy,
  sessionPolicyError,
  type SessionPolicy,
} from "../credentials/session-policy";
import { conversationKeys, invalidateConversation } from "./api";

type BindingTarget = {
  thread_id: string;
  environment_id: string;
  name: string;
};
function bindingApi(
  client: Client,
  workspaceId: string,
  target: BindingTarget,
) {
  const http = client.workspace(workspaceId);
  const params = { path: target };
  return {
    key: [
      ...conversationKeys(workspaceId).root,
      "session-binding",
      target.thread_id,
      target.environment_id,
      target.name,
    ] as const,
    read: async (signal?: AbortSignal) => {
      const result = representation(
        await http.GET(
          "/api/v1/threads/{thread_id}/session-bindings/{environment_id}/{name}",
          { params, signal },
        ),
      );
      if (!result.etag)
        throw new ProtocolError(
          "The Session binding response has no Thread ETag.",
        );
      return { ...result, etag: result.etag };
    },
    update: (configuration: SessionPolicy, etag: string) =>
      http
        .PUT(
          "/api/v1/threads/{thread_id}/session-bindings/{environment_id}/{name}",
          {
            params,
            headers: ifMatch(etag),
            body: { configuration },
          },
        )
        .then(data),
  };
}
type BindingApi = ReturnType<typeof bindingApi>;
type BindingBasis = Awaited<ReturnType<BindingApi["read"]>>;

/** Editing desired policy does not attach or replace the currently running Session. */
export function ThreadSessionBinding({
  thread,
  mount,
  editable,
}: {
  thread: Schema["ThreadView"];
  mount: Schema["MountView"];
  editable: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false),
    [pending, setPending] = useState(false);
  const target = {
    thread_id: thread.id,
    environment_id: mount.environment_id,
    name: mount.session_binding ?? "default",
  };
  return (
    <ModalFrame
      title={t("Session configuration")}
      description={t("Binding {{binding}} · {{environment}}", {
        binding: target.name,
        environment: mount.environment_id,
      })}
      size="lg"
      open={open}
      onOpenChange={setOpen}
      closeLabel={t("Close")}
      trigger={
        <Button type="button" size="sm" variant="outline" disabled={pending}>
          {t("Session configuration")}
        </Button>
      }
    >
      {open && (
        <BindingEditor
          target={target}
          sessionId={thread.session_id}
          editable={editable}
          onPendingChange={setPending}
          close={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function BindingEditor({
  target,
  sessionId,
  editable,
  onPendingChange,
  close,
}: {
  target: BindingTarget;
  sessionId: string;
  editable: boolean;
  onPendingChange: (value: boolean) => void;
  close: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const api = bindingApi(client, workspace.id, target);
  const binding = useQuery({
    queryKey: api.key,
    queryFn: ({ signal }) => api.read(signal),
  });
  return (
    <>
      <ErrorNotice error={binding.error} retry={() => void binding.refetch()} />
      {binding.data ? (
        <BindingForm
          api={api}
          initial={binding.data}
          target={target}
          sessionId={sessionId}
          editable={editable}
          onPendingChange={onPendingChange}
          close={close}
        />
      ) : (
        binding.isPending && <Loading variant="form" />
      )}
    </>
  );
}

function BindingForm({
  api,
  initial,
  target,
  sessionId,
  editable,
  onPendingChange,
  close,
}: {
  api: BindingApi;
  initial: BindingBasis;
  target: BindingTarget;
  sessionId: string;
  editable: boolean;
  onPendingChange: (value: boolean) => void;
  close: () => void;
}) {
  const { t } = useTranslation(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  // The configuration and the Thread ETag are one read; background queries cannot rebase this draft.
  const [basis, setBasis] = useState(initial);
  const [draft, setDraft] = useState(initial.value.configuration);
  const save = useMutation({
    retry: false,
    mutationFn: () => api.update(normalizeSessionPolicy(draft), basis.etag),
    onMutate: () => onPendingChange(true),
    onSettled: () => onPendingChange(false),
    onSuccess: () => {
      void invalidateConversation(cache, workspace.id, {
        threadId: target.thread_id,
        sessionId,
      });
      close();
    },
  });
  const reload = useMutation({
    mutationFn: () => api.read(),
    onSuccess: (current) => {
      setBasis(current);
      save.reset();
    },
  });
  const reviewRequired =
    !!save.error &&
    (!(save.error instanceof ApiError) ||
      save.error.status >= 500 ||
      save.error.status === 412);
  const invalid = sessionPolicyError(draft);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        if (
          editable &&
          !reviewRequired &&
          !invalid &&
          !save.isPending &&
          !reload.isPending
        )
          save.mutate();
      }}
    >
      <p className="text-sm text-muted-foreground">
        {t(
          "Saved configuration applies to later Runs. It does not change an accepted Run or immediately replace its Session. A Run needing a different Session policy can start only after existing work releases the binding.",
        )}
      </p>
      <SessionPolicyFields
        value={draft}
        onChange={setDraft}
        readOnly={!editable || save.isPending || reload.isPending}
      />
      {reviewRequired ? (
        <ConflictNotice
          title={t("Review the current Session configuration")}
          description={t(
            "The Thread changed or the save result is uncertain. Load the current configuration before saving again. Your draft will be kept.",
          )}
          recover={{
            label: t("Load current version"),
            onClick: () => reload.mutate(),
            pending: reload.isPending,
          }}
        />
      ) : (
        <ErrorNotice error={save.error} />
      )}
      <ErrorNotice error={reload.error} />
      {reload.data && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">
            {t(
              "Your draft is unchanged. Saving replaces the current configuration shown below.",
            )}
          </p>
          <JsonView value={reload.data.value.configuration} />
        </div>
      )}
      <FormActions
        pending={save.isPending || reload.isPending}
        disabled={reviewRequired || !!invalid}
        dismiss={!editable}
        label={t("Save Session configuration")}
        onCancel={close}
      />
    </form>
  );
}
