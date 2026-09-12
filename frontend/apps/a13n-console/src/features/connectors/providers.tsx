import { useSuggestedName } from "../../shared/suggested-name";
import { FormSection, formSectionStyles } from "../../shared/form-section";
import { CredentialEditor } from "../../shared/credential-editor";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import { ResourceReference } from "../../shared/resource-reference";
import { ProviderTypeField } from "../../shared/provider-type-field";
import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderIcon } from "../../shared/provider-icon";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import {
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/scope-badge";
import {
  Button,
  FormField,
  ReadOnlyField,
  DisclosureSection,
  Input,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import {
  jsonObject,
  stringValues,
  validateSettings,
} from "../../shared/validation";
import { connectorApi, type ConnectorScope } from "./api";

export function ConnectorProviders({ scope }: { scope: ConnectorScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
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
    <div className={styles.stack}>
      <PageActions>{manage && <ProviderEditor scope={scope} />}</PageActions>
      {rows.selected && (
        <ProviderEditor
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
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            canActivateRow={(item) =>
              (item.workspace_id ? manage : organizationAdmin) ||
              (scope.kind === "workspace" && item.status === "active")
            }
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Provider"),
                tone: "primary",
                render: (item) => (
                  <div className="flex min-w-0 items-center gap-3">
                    <ProviderIcon type={item.type} />
                    <ResourceIdentity
                      name={item.name}
                      description={item.type}
                      resourceId={item.id}
                    />
                  </div>
                ),
              },
              {
                label: t("Scope"),
                tone: "muted",
                render: (item) => (
                  <ScopeBadge workspaceId={item.workspace_id} />
                ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge
                    state={item.status === "active" ? "enabled" : "disabled"}
                  />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No connector providers")}
            description={t(
              "Add an integration-service provider to discover available connectors.",
            )}
          />
        )
      )}
    </div>
  );
}
function ProviderEditor({
  scope,
  providerId,
  controlledOpen,
  onClose,
  finalFocus,
  readOnly = false,
}: ResourceEditorControl & {
  scope: ConnectorScope;
  providerId?: string;
  readOnly?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });

  const definitions = useQuery({
    queryKey: ["connector-provider-types"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-provider-types", { signal })
        .then(data),
  });
  const resource = useQuery({
    queryKey: [
      "connector-providers",
      scope.kind,
      scope.id,
      "detail",
      providerId,
    ],
    enabled: open && !!providerId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-providers/{connector_provider_id}", {
          params: { path: { connector_provider_id: providerId! } },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await resource.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton
            editing={!!providerId}
            createLabel="Add provider"
            editLabel="Edit"
          />
        ) : undefined
      }
      size="lg"
      title={t(providerId ? "Edit provider" : "Add provider")}
      description={
        providerId
          ? undefined
          : t("Connect a service to browse connectors and authorize accounts.")
      }
      closeLabel={t("Close")}
    >
      {open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading />
        ) : definitions.error || resource.error ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
        ) : readOnly && resource.data ? (
          <div className={styles.stack}>
            <ConfigurationSummary value={resource.data.configuration} />
          </div>
        ) : (
          <ProviderForm
            key={generation}
            scope={scope}
            initial={providerId ? resource.data : undefined}
            definitions={definitions.data?.items ?? []}
            close={() => setOpen(false)}
            reload={reload}
          />
        ))}
    </ModalFrame>
  );
}
function ProviderForm({
  scope,
  initial,
  definitions,
  close,
  reload,
}: {
  scope: ConnectorScope;
  initial?: Schema["ConnectorProvider"];
  definitions: Schema["ConnectorProviderDefinition"][];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    { name, setName, suggestName } = useSuggestedName(initial?.name),
    [enabled, setEnabled] = useState(initial?.status === "active"),
    [type, setType] = useState(initial?.type ?? ""),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.configuration ?? {},
    ),
    [credentials, setCredentials] = useState<Record<string, unknown>>({});
  const definition = definitions.find((item) => item.type === type);
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
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormSection>
        <FormField
          className="min-w-0 w-full"
          label={t("Name")}
          labelAction={basis && <ResourceReference id={basis.id} />}
        >
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
      </FormSection>
      <FormSection title={t("Connection")}>
        <ProviderTypeField
          definitions={definitions}
          value={type}
          readOnly={!!basis}
          onValueChange={(value) => {
            setType(value);
            suggestName(
              definitions.find((item) => item.type === value)?.display_name ??
                value,
            );
            setConfiguration({});
            setCredentials({});
          }}
        />
        {type === "composio" && (
          <DisclosureSection
            title={t("Composio setup")}
            summary={t("OAuth and connected accounts")}
          >
            <p className="text-sm text-muted-foreground">
              {t(
                "For OAuth, open that project's Settings → OAuth user verification and set the callback URL to your Service HTTPS origin followed by /connection-authorizations/browser. Local development can use an exact localhost or loopback-IP HTTP origin.",
              )}
            </p>
            <p className="text-sm text-muted-foreground">
              {t(
                "Composio managed apps work without your own OAuth client. To use a custom app or different scopes, create an auth config in Composio Dashboard. Account credentials are collected on Composio's hosted page.",
              )}
            </p>
            <ProviderKeyLink
              href="https://docs.composio.dev/docs/tools-direct/authenticating-tools"
              label="Open setup guide"
            />
          </DisclosureSection>
        )}
        {basis ? (
          <>
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
          </>
        ) : (
          <>
            {definition && (
              <>
                <SchemaFields
                  key={type}
                  schema={definition.configuration_schema}
                  value={configuration}
                  onChange={setConfiguration}
                />
                <SchemaFields
                  secret
                  key={`${type}-credentials`}
                  schema={definition.credential_schema}
                  value={credentials}
                  onChange={setCredentials}
                />
              </>
            )}
          </>
        )}
        {basis &&
          definition &&
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
        {basis && (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
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
              <span className="text-xs text-muted-foreground">
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
          </>
        )}
      </FormSection>
      {basis && (
        <FormSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        </FormSection>
      )}
      <ErrorNotice
        error={save.error}
        retry={basis ? () => void reload() : undefined}
      />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t(basis ? "Save changes" : "Add provider")}
      />
    </form>
  );
}
