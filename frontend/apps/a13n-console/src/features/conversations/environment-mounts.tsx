import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { WorkingDirectory } from "../environments/working-directory";
import { EnvironmentReference } from "../environments/reference";
import {
  conversationKeys,
  conversationQueries,
  invalidateConversation,
} from "./api";
import { isInteractive } from "./transcript/run-actions";

/** The environments the Run's Thread mounts for its later Runs. */
export function RunEnvironmentMounts({ run }: { run: Schema["RunView"] }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const [open, setOpen] = useState(false);
  const thread = useQuery(
    conversationQueries(client, workspace.id).thread(run.thread_id),
  );
  const mounts = useQuery({
    // Mount edits change the Thread, so they refresh with it.
    queryKey: [
      ...conversationKeys(workspace.id).thread(run.thread_id),
      "environments",
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/environments",
          {
            params: {
              path: { workspace_id: workspace.id, thread_id: run.thread_id },
            },
            signal,
          },
        )
        .then(data)
        .then((page) => page.items),
  });
  return (
    <section
      className="flex flex-col gap-3"
      aria-label={t("Additional environments")}
    >
      <div className="flex items-center justify-between gap-2">
        <h3>{t("Additional environments")}</h3>
        {can("run") && thread.data && isInteractive(thread.data) && (
          <ModalFrame
            open={open}
            onOpenChange={setOpen}
            title={t("Add environment")}
            description={t(
              "Mounted environments apply to this thread's later runs. A run keeps the environments it started with.",
            )}
            closeLabel={t("Close")}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon size={14} />
                {t("Add environment")}
              </Button>
            }
          >
            {open && (
              <AddMountForm thread={thread.data} close={() => setOpen(false)} />
            )}
          </ModalFrame>
        )}
      </div>
      <ErrorNotice error={mounts.error ?? thread.error} />
      {mounts.isPending ? (
        <Loading variant="list" rows={1} />
      ) : mounts.data?.length ? (
        <ul className="flex flex-col gap-3">
          {mounts.data.map((mount) => (
            <li key={mount.name} className="flex flex-col gap-1 text-sm">
              <strong>{mount.name}</strong>
              <EnvironmentReference id={mount.environment_id} />
              {mount.working_directory && (
                <span className="break-all">{mount.working_directory}</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        !mounts.error && (
          <p className="text-sm text-muted-foreground">
            {t("No additional environments.")}
          </p>
        )
      )}
    </section>
  );
}

function AddMountForm({
  thread,
  close,
}: {
  thread: Schema["ThreadView"];
  close: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const cache = useQueryClient();
  const [environmentId, setEnvironmentId] = useState("");
  const [name, setName] = useState("");
  const [directory, setDirectory] = useState("");
  const environments = useQuery({
    queryKey: ["mount-environment-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace_id}/environments", {
            params: {
              path: { workspace_id: workspace.id },
              query: { cursor },
            },
            signal,
          })
          .then(data),
      ),
  });
  const selected = environments.data?.find((item) => item.id === environmentId);
  const save = useMutation({
    mutationFn: () =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/environments",
          {
            params: {
              path: { workspace_id: workspace.id, thread_id: thread.id },
            },
            // The mount changes the Thread the reader saw.
            headers: ifMatch(rowTag(thread)),
            body: {
              name,
              environment_id: environmentId,
              ...(directory ? { working_directory: directory } : {}),
            },
          },
        )
        .then(data),
    onSuccess: () => {
      void invalidateConversation(cache, workspace.id, {
        sessionId: thread.session_id,
        threadId: thread.id,
      });
      close();
    },
  });
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice error={environments.error} />
      <ChoiceField
        label={t("Environment")}
        placeholder={t("Select environment")}
        required
        value={environmentId}
        onValueChange={(value) => {
          setEnvironmentId(value);
          setDirectory("");
        }}
        options={(environments.data ?? []).map((item) => ({
          value: item.id,
          label: `${item.name} (${item.id})`,
        }))}
      />
      <FormField
        label={t("Mount name")}
        description={t(
          "A unique name for this Run, such as reference-files. The name workspace is reserved.",
        )}
      >
        <Input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          pattern="[a-z][a-z0-9-]{0,62}"
          maxLength={63}
        />
      </FormField>
      {selected && !selected.template_id && (
        <WorkingDirectory
          key={selected.id}
          value={directory}
          onChange={setDirectory}
        />
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Add environment")}
      />
    </form>
  );
}
