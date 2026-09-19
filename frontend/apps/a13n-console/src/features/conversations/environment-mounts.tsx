import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import { DeviceDirectory } from "../environments/device-directory";
import { EnvironmentReference } from "../environments/reference";
import { conversationQueries } from "./api";

export function RunEnvironmentMounts({ run }: { run: Schema["RunResource"] }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const [open, setOpen] = useState(false);
  const active = run.status === "accepted" || run.status === "running";
  const mayAdd = can("run.steer") && can("environment.use");
  const thread = useQuery({
    ...conversationQueries(client, workspace.id).thread(run.thread_id),
    enabled: active && mayAdd,
  });
  const mounts = useQuery({
    queryKey: ["run-environment-mounts", run.id, run.status],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/runs/{run_id}/environment-mounts", {
            params: { path: { run_id: run.id }, query: { cursor } },
            headers: workspaceHeaders(workspace.id),
            signal,
          })
          .then(data),
      ),
    refetchInterval: active ? 3000 : false,
  });
  return (
    <section
      className="flex flex-col gap-3"
      aria-label={t("Additional environments")}
    >
      <div className="flex items-center justify-between gap-2">
        <h3>{t("Additional environments")}</h3>
        {active && mayAdd && thread.data?.current_run_id === run.id && (
          <ModalFrame
            open={open}
            onOpenChange={setOpen}
            title={t("Add environment")}
            description={t(
              "Accepted additions load before a later model request. The primary environment does not change.",
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
              <AddMountForm runId={run.id} close={() => setOpen(false)} />
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
              <div className="flex items-center justify-between gap-2">
                <strong>{mount.name}</strong>
                <StatePill state={mount.application_status ?? "pending"} />
              </div>
              <EnvironmentReference id={mount.environment_id} />
              {mount.working_directory && (
                <span className="break-all">{mount.working_directory}</span>
              )}
              {mount.error && <p role="alert">{mount.error.message}</p>}
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

function AddMountForm({ runId, close }: { runId: string; close: () => void }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const cache = useQueryClient();
  const key = useIdempotency();
  const [environmentId, setEnvironmentId] = useState("");
  const [name, setName] = useState("");
  const [directory, setDirectory] = useState("");
  const environments = useQuery({
    queryKey: ["mount-environment-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/environments", {
            params: { path: { workspace: workspace.id }, query: { cursor } },
            signal,
          })
          .then(data),
      ),
  });
  const selected = environments.data?.find((item) => item.id === environmentId);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        environment_id: environmentId,
        ...(directory ? { working_directory: directory } : {}),
      };
      return client.http
        .POST("/api/v1/runs/{run_id}/environment-mounts", {
          params: {
            path: { run_id: runId },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["run-environment-mounts", runId],
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
      {selected?.device_id && (
        <DeviceDirectory
          key={selected.id}
          environmentId={selected.id}
          value={directory}
          onChange={setDirectory}
        />
      )}
      <p className="text-sm text-muted-foreground">
        {t(
          "Once accepted, this addition cannot be removed or changed for this Run.",
        )}
      </p>
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Add environment")}
      />
    </form>
  );
}
