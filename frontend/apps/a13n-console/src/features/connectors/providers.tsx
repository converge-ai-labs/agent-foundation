import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ReadOnlyField,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import {
  CatalogStep,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  CredentialEditor,
  FormActions,
  FormSection,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  formSectionStyles,
  jsonObject,
  stringValues,
  validateSettings,
} from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import {
  AddProviderDialog,
  EditProviderDialog,
  ProviderConnectFields,
  ProviderTable,
  credentialDescription,
  credentialHint,
  providerKeyUrls,
  providerStyles,
} from "../providers";
import { connectorApi, type ConnectorScope } from "./api";
import layout from "./connectors.module.css";

type Definition = Schema["ConnectorProviderDefinition"];

function useConnectorDefinitions(enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: ["connector-provider-types"],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-provider-types", { signal })
        .then(data),
  });
}

export function ConnectorProviders({ scope }: { scope: ConnectorScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["ConnectorProvider"]>();
  const query = useQuery({
    queryKey: [
      "connector-providers",
      scope.kind,
      scope.id,
      "list",
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      connectorApi(client, scope).providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("connector_provider.manage");
  return (
    <>
      {rows.selected && (
        <EditConnectorProvider
          key={rows.selected.id}
          scope={
            rows.selected.workspace_id
              ? { kind: "workspace", id: rows.selected.workspace_id }
              : { kind: "organization", id: rows.selected.organization_id }
          }
          providerId={rows.selected.id}
          readOnly={!(rows.selected.workspace_id ? manage : organizationAdmin)}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="connectors"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={manage ? <AddConnectorProvider scope={scope} /> : undefined}
        canActivateRow={(item) =>
          (item.workspace_id ? manage : organizationAdmin) ||
          (scope.kind === "workspace" && item.status === "active")
        }
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          definition: item.type,
          type: item.type,
          workspaceId: item.workspace_id,
          credentials: item.credential_configured
            ? "configured"
            : "not_configured",
          state: item.status === "active" ? "enabled" : "disabled",
        })}
      />
    </>
  );
}

/** Catalog-first creation: choose the broker, then connect it. */
function AddConnectorProvider({ scope }: { scope: ConnectorScope }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const definitions = useConnectorDefinitions();
  // Closing discards the step and the draft, whoever asked for it.
  function change(value: boolean) {
    setOpen(value);
    if (!value) setGeneration((current) => current + 1);
  }
  return (
    <AddProviderDialog<Definition>
      key={generation}
      definitions={definitions.data?.items}
      error={definitions.error}
      open={open}
      onOpenChange={change}
      description={t("Choose the service that brokers your agents' accounts.")}
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
            close={() => change(false)}
            reload={async () => {}}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

function EditConnectorProvider({
  scope,
  providerId,
  controlledOpen,
  onClose,
  finalFocus,
  readOnly = false,
}: ResourceEditorControl & {
  scope: ConnectorScope;
  providerId: string;
  readOnly?: boolean;
}) {
  const client = useClient(),
    [generation, setGeneration] = useState(0);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const definitions = useConnectorDefinitions(state.open);
  const resource = useQuery({
    queryKey: [
      "connector-providers",
      scope.kind,
      scope.id,
      "detail",
      providerId,
    ],
    enabled: state.open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-providers/{connector_provider_id}", {
          params: { path: { connector_provider_id: providerId } },
          signal,
        })
        .then(data),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === resource.data?.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={resource.data?.name}
      id={providerId}
      readOnly={readOnly}
      description={definition?.display_name}
      loading={definitions.isPending || resource.isPending}
      error={definitions.error ?? resource.error}
    >
      {resource.data &&
        (readOnly ? (
          <div className={styles.stack}>
            <ConfigurationSummary
              value={resource.data.configuration}
              schema={definition?.configuration_schema}
            />
          </div>
        ) : (
          <ProviderForm
            key={generation}
            scope={scope}
            initial={resource.data}
            definition={definition}
            close={() => state.setOpen(false)}
            reload={async () => {
              await resource.refetch();
              setGeneration((value) => value + 1);
            }}
          />
        ))}
    </EditProviderDialog>
  );
}

/** Where the broker's own OAuth callback has to point for account authorization. */
function OAuthCallbackSetup() {
  const { t } = useTranslation();
  return (
    <DisclosureSection title={t("OAuth callback setup")}>
      <p className={styles.muted}>
        {t(
          "For OAuth, open that project's Settings → OAuth user verification and set the callback URL to your Service HTTPS origin followed by /connection-authorizations/browser. Local development can use an exact localhost or loopback-IP HTTP origin.",
        )}
      </p>
      <p className={styles.muted}>
        {t(
          "Composio managed apps work without your own OAuth client. To use a custom app or different scopes, create an auth config in Composio Dashboard. Account credentials are collected on Composio's hosted page.",
        )}
      </p>
      <ProviderKeyLink
        href="https://docs.composio.dev/docs/tools-direct/authenticating-tools"
        label="Open setup guide"
      />
    </DisclosureSection>
  );
}

function ProviderForm({
  scope,
  initial,
  definition,
  close,
  reload,
}: {
  scope: ConnectorScope;
  initial?: Schema["ConnectorProvider"];
  definition?: Definition;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    [name, setName] = useState(initial?.name ?? definition?.display_name ?? ""),
    [enabled, setEnabled] = useState(initial?.status === "active"),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.configuration ?? {},
    ),
    [advancedOpen, setAdvancedOpen] = useState(false),
    [credentials, setCredentials] = useState<Record<string, unknown>>({});
  const type = initial?.type ?? definition?.type ?? "";
  function done() {
    void cache.invalidateQueries({ queryKey: ["connector-providers"] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      if (basis) {
        if (Object.keys(credentials).length) {
          if (!definition) throw new Error(t("Provider unavailable."));
          validateSettings(definition.credential_schema, credentials);
        }
        return client.http
          .PATCH("/api/v1/connector-providers/{connector_provider_id}", {
            params: { path: { connector_provider_id: basis.id } },
            body: {
              name,
              status: enabled ? "active" : "disabled",
              expected_version: basis.version,
              ...(Object.keys(credentials).length
                ? { credentials: stringValues(credentials) }
                : {}),
            },
          })
          .then(data);
      }
      if (!definition) throw new Error(t("Select a provider type."));
      validateSettings(definition.configuration_schema, configuration);
      validateSettings(definition.credential_schema, credentials);
      const body = {
        name,
        type,
        configuration: jsonObject(JSON.stringify(configuration)),
        credentials: stringValues(credentials),
      };
      return connectorApi(client, scope).create(body, key.forBody(body));
    },
    onSuccess: done,
  });
  const test = useMutation({
    mutationFn: () => {
      if (!basis) throw new Error(t("Save the provider first."));
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/connector-providers/{connector_provider_id}/test", {
          params: {
            path: { connector_provider_id: basis.id },
            header: commandHeaders(
              scope.kind === "workspace" ? scope.id : undefined,
              key.forBody({ test: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
  });
  function submit(event: React.FormEvent) {
    event.preventDefault();
    save.mutate();
  }
  if (!basis)
    return (
      <form className={providerStyles.connectForm} onSubmit={submit}>
        <ProviderConnectFields
          credentialSchema={definition?.credential_schema}
          configurationSchema={definition?.configuration_schema}
          credential={credentials}
          onCredentialChange={setCredentials}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyUrls[type]}
          advancedOpen={advancedOpen}
          onAdvancedOpenChange={setAdvancedOpen}
        >
          {type === "composio" && <OAuthCallbackSetup />}
        </ProviderConnectFields>
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={t("Add provider")}
        />
      </form>
    );
  return (
    <form className={formSectionStyles.form} onSubmit={submit}>
      <FormSection>
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
      </FormSection>
      <FormSection title={t("Connection")}>
        {type === "composio" && <OAuthCallbackSetup />}
        {Object.keys(configuration).length > 0 && (
          <div className={styles.stack}>
            {typeof configuration.endpoint === "string" && (
              <ReadOnlyField label={t("Endpoint")}>
                {configuration.endpoint}
              </ReadOnlyField>
            )}
            <DisclosureSection title={t("Configuration details")}>
              <ConfigurationSummary
                value={Object.fromEntries(
                  Object.entries(configuration).filter(
                    ([key]) => key !== "endpoint",
                  ),
                )}
                schema={definition?.configuration_schema}
              />
            </DisclosureSection>
          </div>
        )}
        {definition &&
          Object.keys(definition.credential_schema.properties ?? {}).length >
            0 && (
            <CredentialEditor configured={basis.credential_configured}>
              <SchemaFields
                secret
                schema={{ ...definition.credential_schema, required: [] }}
                value={credentials}
                onChange={setCredentials}
              />
            </CredentialEditor>
          )}
        <div className={layout.checkRow}>
          <Button
            size="sm"
            variant="outline"
            loading={test.isPending}
            disabled={
              save.isPending ||
              name !== basis.name ||
              enabled !== (basis.status === "active") ||
              Object.keys(credentials).length > 0
            }
            onClick={() => test.mutate()}
            type="button"
          >
            {t("Check connection")}
          </Button>
          <span className={layout.checkNote}>
            {t("May consume quota or incur cost.")}
          </span>
        </div>
        <ErrorNotice error={test.error} retry={() => void reload()} />
        {test.data && (
          <p role="status" className={styles.muted}>
            {test.data.verified_access
              .map((access) =>
                t(
                  access === "account_read"
                    ? "Connected-account access verified."
                    : "Catalog access verified.",
                ),
              )
              .join(" ")}{" "}
            {t(
              "OAuth callback configuration and upstream account credentials were not tested.",
            )}
          </p>
        )}
      </FormSection>
      <FormSection>
        <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
      </FormSection>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save changes")}
      />
    </form>
  );
}
