import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SettingsRow } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { useCursor } from "../../shared/collection";
import {
  CatalogStep,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import {
  AddProviderDialog,
  CredentialRow,
  EditProviderDialog,
  ProviderConnectFields,
  ProviderEditor,
  ProviderFacts,
  ProviderGroup,
  ProviderName,
  ProviderReadOnly,
  ProviderTable,
  credentialDescription,
  credentialHint,
  credentialLabel,
  providerKeyUrls,
  providerStyles,
} from "../providers";
import { environmentApi, type EnvironmentScope } from "./api";

type Definition = Schema["EnvironmentProviderDefinition"];

/** A provider the running Service owns: readable here, changed by the operator. */
const deploymentNote =
  "Connection settings come from the running Service and cannot be edited here.";

export function useEnvironmentTypes() {
  const client = useClient(),
    { workspace } = useAccess();
  return useQuery({
    queryKey: ["environment-types", workspace?.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-provider-types", {
          headers: workspace ? workspaceHeaders(workspace.id) : undefined,
          signal,
        })
        .then(data),
  });
}
function schema(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value))
    : {};
}

export function EnvironmentProviders({ scope }: { scope: EnvironmentScope }) {
  const providerTypes = useEnvironmentTypes();
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor(),
    api = environmentApi(client, scope);
  const rows = useResourceRows<Schema["EnvironmentProvider"]>();
  const query = useQuery({
    queryKey: [
      "environment-providers",
      scope.kind,
      scope.id,
      "list",
      page.cursor,
    ],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("environment_provider.manage");
  const connectable = (providerTypes.data?.items ?? []).filter(
    (type) => !type.deployment_managed,
  );
  const add =
    manage && connectable.length ? (
      <AddEnvironmentProvider scope={scope} definitions={connectable} />
    ) : undefined;
  const definitionFor = (type: string) =>
    providerTypes.data?.items.find((entry) => entry.type === type);
  return (
    <>
      {rows.selected && (
        <EditEnvironmentProvider
          key={rows.selected.id}
          scope={
            rows.selected.workspace_id
              ? { kind: "workspace", id: rows.selected.workspace_id }
              : { kind: "organization", id: rows.selected.organization_id }
          }
          provider={rows.selected}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="environments"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        canActivateRow={(item) =>
          item.configuration_source === "deployment" ||
          (item.workspace_id ? manage : organizationAdmin)
        }
        onRowActivate={rows.activate}
        notice={
          <p className={providerStyles.notice}>
            {t(
              "Direct Local and Docker require a single-host deployment and operator configuration.",
            )}
          </p>
        }
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition:
            item.configuration_source === "deployment"
              ? t("Configured by deployment")
              : (definitionFor(item.type)?.display_name ?? item.type),
          workspaceId: item.workspace_id,
          credentials:
            definitionFor(item.type)?.credential_schema == null
              ? ("not_required" as const)
              : item.credential_configured
                ? ("configured" as const)
                : ("not_configured" as const),
          state: item.enabled ? "enabled" : "disabled",
          editLabel:
            item.configuration_source === "deployment"
              ? t("View details")
              : undefined,
        })}
      />
    </>
  );
}

/** Catalog-first creation for the providers an operator can connect. */
function AddEnvironmentProvider({
  scope,
  definitions,
}: {
  scope: EnvironmentScope;
  definitions: Definition[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  // Closing discards the step and the draft, whoever asked for it.
  function change(value: boolean) {
    setOpen(value);
    if (!value) setGeneration((current) => current + 1);
  }
  return (
    <AddProviderDialog<Definition>
      key={generation}
      definitions={definitions}
      open={open}
      onOpenChange={change}
      description={t("Choose where your environments run.")}
      hint={(definition) => credentialHint(definition.credential_schema)}
      connectDescription={(definition) =>
        t(credentialDescription(definition.credential_schema), {
          provider: definition.display_name,
        })
      }
    >
      {(definition, back) => (
        <CatalogStep backLabel={t("All providers")} onBack={back}>
          <ProviderForm
            scope={scope}
            definition={definition}
            definitions={definitions}
            close={() => change(false)}
            reload={async () => {}}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

function EditEnvironmentProvider({
  scope,
  provider,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  scope: EnvironmentScope;
  provider: Schema["EnvironmentProvider"];
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    definitions = useEnvironmentTypes();
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const query = useQuery({
    queryKey: [
      "environment-providers",
      scope.kind,
      scope.id,
      "detail",
      provider.id,
    ],
    enabled: state.open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-providers/{resource_id}", {
          params: { path: { resource_id: provider.id } },
          signal,
        })
        .then(representation),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === provider.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={query.data?.value.name ?? provider.name}
      id={provider.id}
      type={provider.type}
      definition={definition?.display_name}
      scope={provider.workspace_id ? "workspace" : "organization"}
      readOnly={provider.configuration_source === "deployment"}
      description={
        provider.configuration_source === "deployment"
          ? t(deploymentNote)
          : undefined
      }
      loading={definitions.isPending || query.isPending}
      error={definitions.error ?? query.error}
    >
      {query.data && (
        <ProviderForm
          key={generation}
          scope={scope}
          initial={query.data}
          definitions={definitions.data?.items ?? []}
          close={() => state.setOpen(false)}
          reload={async () => {
            await query.refetch();
            setGeneration((value) => value + 1);
          }}
        />
      )}
    </EditProviderDialog>
  );
}

function ProviderForm({
  scope,
  initial,
  definition: chosen,
  definitions,
  close,
  reload,
}: {
  scope: EnvironmentScope;
  initial?: ReturnType<typeof representation<Schema["EnvironmentProvider"]>>;
  definition?: Definition;
  definitions: Definition[];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    [basis] = useState(initial),
    [name, setName] = useState(
      initial?.value.name ?? chosen?.display_name ?? "",
    ),
    type = initial?.value.type ?? chosen?.type ?? "",
    [enabled, setEnabled] = useState(initial?.value.enabled ?? true),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.value.configuration ?? {},
    ),
    [removeCredential, setRemoveCredential] = useState(false),
    [advancedOpen, setAdvancedOpen] = useState(false),
    [credential, setCredential] = useState<Record<string, unknown>>({});
  const deployment = basis?.value.configuration_source === "deployment";
  const definition = definitions.find((item) => item.type === type) ?? chosen,
    configSchema = schema(definition?.configuration_schema),
    credentialSchema = schema(definition?.credential_schema);
  const connectivity = useQuery({
    queryKey: ["environment-provider-connectivity", basis?.value.id],
    enabled: basis?.value.type === "docker",
    refetchInterval: 5000,
    queryFn: () =>
      client.http
        .GET("/api/v1/environment-providers/{provider_id}/connectivity", {
          params: { path: { provider_id: basis!.value.id } },
        })
        .then(data),
  });
  function done() {
    void cache.invalidateQueries({ queryKey: ["environment-providers"] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      if (basis) {
        if (!removeCredential && Object.keys(credential).length)
          validateSettings(credentialSchema, credential);
        return client.http
          .PATCH("/api/v1/environment-providers/{provider_id}", {
            params: {
              path: { provider_id: basis.value.id },
              header: { "If-Match": basis.etag ?? "" },
            },
            body: {
              name,
              enabled,
              ...(removeCredential
                ? { credential: null }
                : Object.keys(credential).length
                  ? { credential: jsonObject(JSON.stringify(credential)) }
                  : {}),
            },
          })
          .then(data);
      }
      validateSettings(configSchema, configuration);
      if (Object.keys(credential).length)
        validateSettings(credentialSchema, credential);
      return environmentApi(client, scope).createProvider({
        name,
        type,
        configuration: jsonObject(JSON.stringify(configuration)),
        ...(Object.keys(credential).length && {
          credential: jsonObject(JSON.stringify(credential)),
        }),
      });
    },
    onSuccess: done,
  });
  if (!basis)
    return (
      <form
        className={providerStyles.connectForm}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ProviderConnectFields
          credentialSchema={credentialSchema}
          configurationSchema={configSchema}
          credential={credential}
          onCredentialChange={setCredential}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyUrls[type]}
          advancedOpen={advancedOpen}
          onAdvancedOpenChange={setAdvancedOpen}
        />
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={t("Add provider")}
        />
      </form>
    );
  const engine = basis.value.type === "docker" && (
    <SettingsRow stackOnNarrow={false} label={t("Engine")}>
      <span className={providerStyles.fact} role="status">
        {connectivity.data?.error && (
          <span className={providerStyles.factValue}>
            {connectivity.data.error}
          </span>
        )}
        <StatePill state={connectivity.data?.status ?? "unknown"} />
      </span>
    </SettingsRow>
  );
  const credentials = Object.keys(schema(credentialSchema.properties)).length
    ? basis.value.credential_configured
      ? ("configured" as const)
      : ("not_configured" as const)
    : ("not_required" as const);
  if (deployment)
    return (
      <ProviderReadOnly
        hideDefaults
        enabled={basis.value.enabled}
        credentials={credentials}
        configuration={configuration}
        schema={configSchema}
        facts={engine}
        onClose={close}
      />
    );
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ProviderName value={name} onChange={setName} />
      <ProviderGroup>
        <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        {credentials !== "not_required" && (
          <CredentialRow
            label={t(credentialLabel(credentialSchema))}
            configured={!!basis.value.credential_configured}
            removing={removeCredential}
            onRemovingChange={setRemoveCredential}
            onDiscard={() => setCredential({})}
          >
            <SchemaFields
              secret
              autoFocus
              labelAction={
                providerKeyUrls[type] && (
                  <ProviderKeyLink {...providerKeyUrls[type]} />
                )
              }
              schema={{ ...credentialSchema, required: [] }}
              value={credential}
              onChange={setCredential}
            />
          </CredentialRow>
        )}
        {engine}
        <ProviderFacts
          hideDefaults
          configuration={configuration}
          schema={configSchema}
        />
      </ProviderGroup>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save changes")}
      />
    </ProviderEditor>
  );
}
