import { EnvironmentDetails } from "./instance-details";
import { ResourceIdentity } from "../../shared/collection";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/form";
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
    [stateVersion, setStateVersion] = useState("1");
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
