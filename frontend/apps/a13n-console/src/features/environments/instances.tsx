import { EnvironmentNameEditor } from "./instance-name";
import { ResourceIdentity } from "../../shared/collection";
import { ProviderIcon } from "../../shared/provider-icon";
import { CopyableId } from "../../shared/copy";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ReadOnlyField,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  representation,
  type Schema,
} from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  ErrorToast,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions, TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { jsonObject, jsonValue } from "../../shared/validation";
import { environmentApi } from "./api";
import { useEnvironmentTypes } from "./providers";

export function EnvironmentInstances() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["environments", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/environments", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
    refetchInterval: 15_000,
  });
  return (
    <div className={styles.stack}>
      <PageActions>
        {can("environment.manage") && <CreateEnvironment />}
      </PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={6} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Environment"),
                tone: "primary",
                render: (item) => (
                  <>
                    <ResourceIdentity name={item.name} resourceId={item.id} />
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
              {
                label: t("Generation"),
                align: "right",
                render: (item) => item.generation,
              },
              {
                label: t("Activity"),
                render: (item) => (
                  <StateBadge state={item.retention_condition} />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) => <Timestamp value={item.updated_at} />,
              },
              {
                label: t("Actions"),
                align: "right",
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
    [nameEditorKey, setNameEditorKey] = useState(0),
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
        .then(representation),
  });
  const provider = useQuery({
    queryKey: ["environment-provider", environment.provider_id],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-providers/{resource_id}", {
          params: { path: { resource_id: environment.provider_id } },
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
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Details")}
        </Button>
      }
      size={"md"}
      title={t("Environment details")}
      closeLabel={t("Close")}
      open={open}
    >
      <div className={styles.stack}>
        <ErrorNotice error={detail.error} />
        <ErrorToast error={command.error} />
        {detail.isPending ? (
          <Loading variant="form" rows={5} />
        ) : (
          detail.data && (
            <>
              <ResourceIdentity
                name={detail.data.value.name}
                resourceId={detail.data.value.id}
              />
              {can("environment.manage") && (
                <EnvironmentNameEditor
                  key={nameEditorKey}
                  environment={detail.data.value}
                  etag={detail.data?.etag}
                  reload={async () => {
                    const result = await detail.refetch();
                    if (result.isSuccess)
                      setNameEditorKey((value) => value + 1);
                  }}
                />
              )}
              <div className={styles.twoColumns}>
                <ReadOnlyField label={t("Status")}>
                  <StateBadge state={detail.data.value.status} />
                </ReadOnlyField>
                <ReadOnlyField label={t("Activity")}>
                  <StateBadge state={detail.data.value.retention_condition} />
                </ReadOnlyField>
                <ReadOnlyField label={t("Generation")}>
                  {detail.data.value.generation}
                </ReadOnlyField>
                <ReadOnlyField label={t("Access ceiling")}>
                  {t(
                    detail.data.value.access === "full"
                      ? "Full access"
                      : detail.data.value.access === "read_only"
                        ? "Read only"
                        : "Read and write",
                  )}
                </ReadOnlyField>
                <ReadOnlyField label={t("Updated")}>
                  <Timestamp value={detail.data.value.updated_at} />
                </ReadOnlyField>
              </div>
              <div className={`${styles.stack} border-t border-border pt-4`}>
                <ReadOnlyField label={t("Ownership")}>
                  {t(
                    detail.data.value.ownership === "managed"
                      ? "Managed"
                      : "External",
                  )}
                </ReadOnlyField>
                <ReadOnlyField label={t("Provider")}>
                  {provider.data ? (
                    <ResourceIdentity
                      name={provider.data.name}
                      resourceId={provider.data.id}
                      icon={<ProviderIcon type={provider.data.type} />}
                    />
                  ) : (
                    <CopyableId value={detail.data.value.provider_id} />
                  )}
                </ReadOnlyField>
                {detail.data.value.template_revision_id && (
                  <ReadOnlyField label={t("Template revision")}>
                    <CopyableId
                      value={detail.data.value.template_revision_id}
                    />
                  </ReadOnlyField>
                )}
              </div>
            </>
          )
        )}
        {command.data && (
          <div role="status">
            <strong>{t("Lifecycle command")}</strong>{" "}
            <StateBadge state={command.data.status} />
            <small>{command.data.id}</small>
          </div>
        )}
        {can("environment.manage") && environment.ownership === "managed" && (
          <div className={`${styles.actions} border-t border-border pt-4`}>
            <Confirm
              subject={environment.id}
              title={t("Stop environment target")}
              description={t(
                "This stops the target when it has no active users. A later run can resume it.",
              )}
              trigger={t("Stop target")}
              action={() => act("stop")}
            />
            <Confirm
              subject={environment.id}
              title={t("Delete environment target")}
              description={t(
                "Files and processes on the target will be lost. Environment history is retained. This cannot be undone.",
              )}
              trigger={t("Delete target")}
              danger
              triggerVariant="outline"
              action={() => act("delete")}
            />
          </div>
        )}
      </div>
    </ModalFrame>
  );
}
function CreateEnvironment() {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="default" type="button">
          {t("Create environment")}
        </Button>
      }
      size={"md"}
      title={t("Create environment")}
      description={t(
        "Allocate from a template or connect an externally managed target.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && <EnvironmentForm close={() => setOpen(false)} />}
    </ModalFrame>
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
    [name, setName] = useState(""),
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
        | Schema["CreateManagedEnvironmentRequest"]
        | Schema["RegisterEnvironmentRequest"] =
        kind === "managed"
          ? {
              template_id: templateId,
              ...(name.trim() && { name: name.trim() }),
              ...(version && { version: Number(version) }),
            }
          : {
              provider_id: providerId,
              ...(name.trim() && { name: name.trim() }),
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
        .POST("/api/v1/workspaces/{workspace}/environments", {
          params: {
            path: { workspace: workspace.id },
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
      <FormField
        label={t("Name")}
        description={t("Leave empty to generate a name.")}
      >
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <ChoiceField
        placeholder={t("Select ownership")}
        value={kind}
        className="min-w-0"
        onValueChange={setKind}
        label={t("Ownership")}
        options={[
          { value: "managed", label: t("Managed from template") },
          { value: "external", label: t("External target") },
        ]}
      />
      <ErrorNotice error={templates.error ?? providers.error ?? types.error} />
      {kind === "managed" ? (
        <>
          <ChoiceField
            placeholder={t("Select template")}
            value={templateId}
            className="min-w-0"
            required
            onValueChange={setTemplateId}
            label={t("Template")}
            options={
              templates.data
                ?.filter((item) => !item.archived_at)
                .map((item) => ({ value: item.id, label: item.name })) ?? []
            }
          />
          <FormField
            className="min-w-0 w-full"
            label={t("Template version (optional)")}
            description={t("Leave empty to select the current revision.")}
          >
            <Input
              type="number"
              min={1}
              step={1}
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
          </FormField>
        </>
      ) : (
        <>
          <ChoiceField
            placeholder={t("Select provider")}
            value={providerId}
            className="min-w-0"
            required
            onValueChange={setProviderId}
            label={t("Provider")}
            options={
              providers.data
                ?.filter((item) => item.enabled)
                .map((item) => ({ value: item.id, label: item.name })) ?? []
            }
          />
          <ChoiceField
            placeholder={t("Select access")}
            value={access}
            className="min-w-0"
            onValueChange={(value) => {
              if (
                value === "full" ||
                value === "read_only" ||
                value === "read_write"
              )
                setAccess(value);
            }}
            label={t("Access ceiling")}
            options={[
              { value: "full", label: t("Full access") },
              { value: "read_write", label: t("Read and write") },
              { value: "read_only", label: t("Read only") },
            ]}
          />
          <FormField
            className="min-w-0 w-full"
            label={t("Configuration schema version")}
          >
            <Input
              required={true}
              value={schemaVersion}
              onChange={(event) => setSchemaVersion(event.target.value)}
            />
          </FormField>
          <TextAreaField
            label={t("Connection configuration (JSON)")}
            value={configuration}
            onChange={setConfiguration}
            code
          />
          <DisclosureSection
            title={<>{t("Existing target state (optional)")}</>}
          >
            <FormField className="min-w-0 w-full" label={t("State version")}>
              <Input
                value={stateVersion}
                onChange={(event) => setStateVersion(event.target.value)}
              />
            </FormField>
            <TextAreaField
              label={t("Provider state (JSON)")}
              value={state}
              onChange={setState}
              code
            />
          </DisclosureSection>
        </>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Create environment")}
      />
    </form>
  );
}
