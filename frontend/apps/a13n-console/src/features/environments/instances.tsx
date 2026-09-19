import { MonitorIcon, PlusIcon } from "@phosphor-icons/react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import {
  FormActions,
  jsonObject,
  jsonValue,
  TextAreaField,
} from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { environmentApi } from "./api";
import instanceStyles from "./environments.module.css";
import { EnvironmentPanel } from "./instance-details";
import { useEnvironmentTypes } from "./providers";

/** The environments that exist right now, with their lifecycle state. */
export function EnvironmentInstances() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [selected, setSelected] = useState<Schema["Environment"]>();
  const scope = { kind: "workspace", id: workspace.id } as const;
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
  const providers = useQuery({
    queryKey: ["environment-provider-options", "workspace", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, scope).providers(signal, cursor),
      ),
    enabled: can("environment_provider.read"),
  });
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, scope).templates(signal, cursor),
      ),
    enabled: can("environment_template.read"),
  });
  const revisionIds = [
    ...new Set(
      (query.data?.items ?? []).flatMap((item) =>
        item.template_revision_id ? [item.template_revision_id] : [],
      ),
    ),
  ];
  const revisions = useQueries({
    queries: revisionIds.map((id) => ({
      queryKey: ["environment-revision", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        client.http
          .GET("/api/v1/environment-template-revisions/{revision_id}", {
            params: { path: { revision_id: id } },
            signal,
          })
          .then(data),
    })),
  });
  const providerById = new Map(
    providers.data?.map((provider) => [provider.id, provider]),
  );
  const templateById = new Map(
    templates.data?.map((template) => [template.id, template]),
  );
  const revisionById = new Map(
    revisions.flatMap((revision) =>
      revision.data ? [[revision.data.id, revision.data] as const] : [],
    ),
  );
  const resolving =
    templates.isPending || revisions.some((entry) => entry.isPending);
  function templateName(item: Schema["Environment"]) {
    if (!item.template_revision_id) return t("External target");
    const revision = revisionById.get(item.template_revision_id);
    const template = revision && templateById.get(revision.template_id);
    if (template) return template.name;
    return resolving ? <InlineLoading width="6rem" /> : t("Managed");
  }
  return (
    <div className={styles.stack}>
      <PageActions>
        {can("environment.manage") && <CreateEnvironment />}
      </PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Environment instances")}
            items={query.data.items}
            onRowActivate={(item) => setSelected(item)}
            columns={[
              {
                label: t("Environment"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    icon={
                      <MonitorIcon
                        aria-hidden="true"
                        className="size-4 text-muted-foreground"
                      />
                    }
                    description={templateName(item)}
                  />
                ),
              },
              {
                label: t("Provider"),
                render: (item) => {
                  const provider = providerById.get(item.provider_id);
                  if (!provider)
                    return providers.isPending &&
                      providers.fetchStatus !== "idle" ? (
                      <InlineLoading width="5rem" />
                    ) : (
                      <span className={styles.muted}>{t("Not available")}</span>
                    );
                  return (
                    <span className={instanceStyles.providerCell}>
                      <ProviderIcon type={provider.type} />
                      <span>{provider.name}</span>
                    </span>
                  );
                },
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Activity"),
                render: (item) => (
                  <StatePill state={item.retention_condition} />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) => <Timestamp value={item.updated_at} />,
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} environments on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<MonitorIcon aria-hidden="true" />}
            title={t("No environments yet")}
            description={t(
              "Choose an environment template when starting a conversation, or register an external environment.",
            )}
            action={
              can("environment.manage") ? <CreateEnvironment /> : undefined
            }
          />
        )
      )}
      {selected && (
        <EnvironmentPanel
          key={selected.id}
          environment={selected}
          open
          onClose={() => setSelected(undefined)}
        />
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
          <PlusIcon aria-hidden="true" />
          {t("Create environment")}
        </Button>
      }
      size={"md"}
      placement="top"
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
    key = useIdempotency();
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
  const types = useEnvironmentTypes();
  const [kind, setKind] = useState("managed"),
    [name, setName] = useState(""),
    [templateId, setTemplateId] = useState(""),
    [providerId, setProviderId] = useState(""),
    [version, setVersion] = useState(""),
    [schemaVersion, setSchemaVersion] = useState("1"),
    [configuration, setConfiguration] = useState("{}"),
    [deviceId, setDeviceId] = useState(""),
    [state, setState] = useState(""),
    [stateVersion, setStateVersion] = useState("1");
  const provider = providers.data?.find((item) => item.id === providerId);
  const isDevice =
    provider?.type === "a13n.http-envd" ||
    provider?.type === "a13n.websocket-envd";
  const save = useMutation({
    mutationFn: () => {
      if (kind === "managed" && !templateId)
        throw new Error(t("Select an environment template."));
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
              configuration: isDevice ? {} : jsonObject(configuration),
              configuration_schema_version: isDevice ? "1" : schemaVersion,
              ...(isDevice && { device_id: deviceId.trim() }),
              ...(!isDevice &&
                state.trim() &&
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
  const templateOptions =
    templates.data
      ?.filter((item) => !item.archived_at)
      .map((item) => ({
        value: item.id,
        label: item.name,
        description: item.description ?? undefined,
        badge: t("Version {{version}}", { version: item.version }),
      })) ?? [];
  const providerOptions =
    providers.data
      ?.filter((item) => item.enabled)
      .map((item) => ({
        value: item.id,
        label: item.name,
        icon: <ProviderIcon type={item.type} />,
        description:
          types.data?.items.find((definition) => definition.type === item.type)
            ?.display_name ?? undefined,
      })) ?? [];
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
      <FormField label={t("Ownership")}>
        <SearchPicker
          label={t("Ownership")}
          placeholder={t("Select ownership")}
          emptyMessage={t("No options")}
          value={kind}
          onValueChange={setKind}
          groups={[
            {
              label: t("Ownership"),
              options: [
                {
                  value: "managed",
                  label: t("Managed from template"),
                  description: t(
                    "Service allocates and retires the target for you.",
                  ),
                },
                {
                  value: "external",
                  label: t("External target"),
                  description: t(
                    "Register a target you run and keep control of.",
                  ),
                },
              ],
            },
          ]}
        />
      </FormField>
      <ErrorNotice error={templates.error ?? providers.error ?? types.error} />
      {kind === "managed" ? (
        <>
          <FormField label={t("Template")}>
            <SearchPicker
              label={t("Template")}
              placeholder={t("Select template")}
              emptyMessage={t("No matching templates")}
              value={templateId}
              onValueChange={setTemplateId}
              groups={[{ label: t("Templates"), options: templateOptions }]}
            />
          </FormField>
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
          <FormField label={t("Provider")}>
            <SearchPicker
              label={t("Provider")}
              placeholder={t("Select provider")}
              emptyMessage={t("No matching providers")}
              value={providerId}
              onValueChange={setProviderId}
              groups={[{ label: t("Providers"), options: providerOptions }]}
            />
          </FormField>
          {isDevice ? (
            <FormField
              label={t("Device ID")}
              description={t(
                "Use the device_id configured in envd. The provider owns the connection; each Run chooses its directory.",
              )}
            >
              <Input
                required
                value={deviceId}
                onChange={(event) => setDeviceId(event.target.value)}
              />
            </FormField>
          ) : (
            <DisclosureSection
              title={t("Provider configuration (JSON)")}
              defaultOpen
            >
              <div className={styles.stack}>
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
                <FormField
                  className="min-w-0 w-full"
                  label={t("State version")}
                >
                  <Input
                    value={stateVersion}
                    onChange={(event) => setStateVersion(event.target.value)}
                  />
                </FormField>
                <TextAreaField
                  label={t("Provider state (JSON)")}
                  hint={t(
                    "Leave empty unless you are adopting a target that already exists.",
                  )}
                  value={state}
                  onChange={setState}
                  code
                />
              </div>
            </DisclosureSection>
          )}
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
