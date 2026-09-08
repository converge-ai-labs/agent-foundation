import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Empty,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions, JsonView, TextArea } from "../../shared/form";
import { Table, Pagination, useCursor } from "../../shared/collection";
import { jsonObject, jsonValue } from "../../shared/validation";
import { useIdempotency } from "../../shared/idempotency";
import { environmentApi } from "./api";
import { useEnvironmentTypes } from "./providers";
import styles from "../../shared/shared.module.css";

export function EnvironmentInstances() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["environments", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/environments", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
    refetchInterval: 15_000,
  });
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          {t("Working environments shared by threads in this workspace.")}
        </p>
        {can("environment.manage") && <CreateEnvironment />}
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Environment"),
                render: (item) => (
                  <>
                    <code>{item.id}</code>
                    <small>
                      {t(item.ownership === "managed" ? "Managed" : "External")}
                    </small>
                  </>
                ),
              },
              {
                label: t("Status"),
                render: (item) => <StateBadge state={item.status} />,
              },
              { label: t("Generation"), render: (item) => item.generation },
              {
                label: t("Activity"),
                render: (item) => (
                  <StateBadge state={item.retention_condition} />
                ),
              },
              {
                label: t("Updated"),
                render: (item) => <Timestamp value={item.updated_at} />,
              },
              {
                label: t("Actions"),
                render: (item) => <EnvironmentDetails environment={item} />,
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No environments yet")}
            description={t(
              "Choose an environment template when starting a conversation, or register an external environment.",
            )}
          />
        )
      )}
    </div>
  );
}
function EnvironmentDetails({
  environment,
}: {
  environment: Schema["Environment"];
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [commandId, setCommandId] = useState<string>(),
    key = useIdempotency();
  const detail = useQuery({
    queryKey: ["environment", environment.id],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environments/{resource_id}", {
          params: { path: { resource_id: environment.id } },
          signal,
        })
        .then(data),
  });
  const command = useQuery({
    queryKey: ["environment-command", commandId],
    enabled: !!commandId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-commands/{command_id}", {
          params: { path: { command_id: commandId! } },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 2000 : false,
  });
  async function act(action: "stop" | "delete") {
    const params = {
      path: { environment_id: environment.id },
      header: commandHeaders(
        workspace.id,
        key.forBody({ action, id: environment.id }),
      ),
    };
    const receipt =
      action === "stop"
        ? data(
            await client.http.POST(
              "/api/v1/environments/{environment_id}/stop",
              { params },
            ),
          )
        : data(
            await client.http.POST(
              "/api/v1/environments/{environment_id}/delete",
              { params },
            ),
          );
    setCommandId(receipt.id);
  }
  return (
    <Dialog
      title={t("Environment details")}
      description={t(
        "Deleting a managed target removes its files. Later use may rebuild an empty target from the original recipe.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={setOpen}
      trigger={<Button size="sm">{t("Details")}</Button>}
    >
      <div className={styles.stack}>
        <ErrorNotice error={detail.error ?? command.error} />
        {detail.isPending ? <Loading /> : <JsonView value={detail.data} />}
        {command.data && (
          <div role="status">
            <strong>{t("Lifecycle command")}</strong>{" "}
            <StateBadge state={command.data.status} />
            <small>{command.data.id}</small>
          </div>
        )}
        {can("environment.manage") && environment.ownership === "managed" && (
          <div className={styles.actions}>
            <Confirm
              title={t("Stop environment target")}
              description={t(
                "This stops the target when it has no active users. A later run can resume it.",
              )}
              trigger={t("Stop target")}
              action={() => act("stop")}
            />
            <Confirm
              title={t("Delete environment target")}
              description={t(
                "Files and processes on the target will be lost. Environment history is retained. This cannot be undone.",
              )}
              trigger={t("Delete target")}
              danger
              action={() => act("delete")}
            />
          </div>
        )}
      </div>
    </Dialog>
  );
}
function CreateEnvironment() {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t("Create environment")}
      description={t(
        "Allocate from a template or connect an externally managed target.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button variant="primary" size="sm">
          {t("Create environment")}
        </Button>
      }
    >
      {open && <EnvironmentForm close={() => setOpen(false)} />}
    </Dialog>
  );
}
function EnvironmentForm({ close }: { close: () => void }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    types = useEnvironmentTypes();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, scope).templates(signal, cursor),
      ),
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", "workspace", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, scope).providers(signal, cursor),
      ),
  });
  const [kind, setKind] = useState("managed"),
    [templateId, setTemplateId] = useState(""),
    [providerId, setProviderId] = useState(""),
    [version, setVersion] = useState(""),
    [schemaVersion, setSchemaVersion] = useState("1"),
    [configuration, setConfiguration] = useState("{}"),
    [state, setState] = useState(""),
    [stateVersion, setStateVersion] = useState("1"),
    [access, setAccess] = useState<Schema["EnvironmentAccess"]>("full");
  const save = useMutation({
    mutationFn: () => {
      const provider = providers.data?.find((item) => item.id === providerId);
      if (kind === "external" && !provider)
        throw new Error(t("Select an environment provider."));
      const body:
        | Schema["NewEnvironmentSelection"]
        | Schema["RegisterEnvironmentRequest"] =
        kind === "managed"
          ? {
              template_id: templateId,
              ...(version && { version: Number(version) }),
            }
          : {
              provider_id: providerId,
              configuration: jsonObject(configuration),
              configuration_schema_version: schemaVersion,
              access,
              ...(state.trim() &&
                provider && {
                  state: {
                    provider_key: provider.type,
                    state_version: stateVersion,
                    state: jsonValue(state),
                  },
                }),
            };
      return client.http
        .POST("/api/v1/workspaces/{workspace_id}/environments", {
          params: {
            path: { workspace_id: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      close();
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <SelectField
        label={t("Ownership")}
        placeholder={t("Select ownership")}
        value={kind}
        onValueChange={setKind}
        options={[
          { value: "managed", label: t("Managed from template") },
          { value: "external", label: t("External target") },
        ]}
      />
      <ErrorNotice error={templates.error ?? providers.error ?? types.error} />
      {kind === "managed" ? (
        <>
          <SelectField
            label={t("Template")}
            placeholder={t("Select template")}
            required
            value={templateId}
            onValueChange={setTemplateId}
            options={
              templates.data
                ?.filter((item) => !item.archived_at)
                .map((item) => ({ value: item.id, label: item.name })) ?? []
            }
          />
          <Input
            label={t("Template version (optional)")}
            hint={t("Leave empty to select the current revision.")}
            type="number"
            min={1}
            step={1}
            value={version}
            onChange={(event) => setVersion(event.target.value)}
          />
        </>
      ) : (
        <>
          <SelectField
            label={t("Provider")}
            placeholder={t("Select provider")}
            required
            value={providerId}
            onValueChange={setProviderId}
            options={
              providers.data
                ?.filter((item) => item.enabled)
                .map((item) => ({ value: item.id, label: item.name })) ?? []
            }
          />
          <SelectField
            label={t("Access ceiling")}
            placeholder={t("Select access")}
            value={access}
            onValueChange={(value) => {
              if (
                value === "full" ||
                value === "read_only" ||
                value === "read_write"
              )
                setAccess(value);
            }}
            options={[
              { value: "full", label: t("Full access") },
              { value: "read_write", label: t("Read and write") },
              { value: "read_only", label: t("Read only") },
            ]}
          />
          <Input
            label={t("Configuration schema version")}
            value={schemaVersion}
            onChange={(event) => setSchemaVersion(event.target.value)}
            required
          />
          <TextArea
            label={t("Connection configuration (JSON)")}
            value={configuration}
            onChange={setConfiguration}
            code
          />
          <details>
            <summary>{t("Existing target state (optional)")}</summary>
            <Input
              label={t("State version")}
              value={stateVersion}
              onChange={(event) => setStateVersion(event.target.value)}
            />
            <TextArea
              label={t("Provider state (JSON)")}
              value={state}
              onChange={setState}
              code
            />
          </details>
        </>
      )}
      <ErrorNotice error={save.error} />
      <FormActions pending={save.isPending} label={t("Create environment")} />
    </form>
  );
}
