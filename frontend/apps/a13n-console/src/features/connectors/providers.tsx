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
import { useAccess } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import {
  jsonObject,
  stringValues,
  validateSettings,
} from "../../shared/validation";
import { connectorApi, type ConnectorScope } from "./api";
import { ConnectorCatalog } from "./catalog";

export function ConnectorProviders({ scope }: { scope: ConnectorScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor();
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
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Provider"),
                render: (item) => (
                  <>
                    <strong>{item.name}</strong>
                    <small>{item.type}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) =>
                  t(item.workspace_id ? "Workspace" : "Organization"),
              },
              {
                label: t("Status"),
                render: (item) => <StateBadge state={item.status} />,
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <div className={styles.actions}>
                    {(item.workspace_id ? manage : organizationAdmin) && (
                      <ProviderEditor
                        scope={
                          item.workspace_id
                            ? { kind: "workspace", id: item.workspace_id }
                            : { kind: "organization", id: item.organization_id }
                        }
                        providerId={item.id}
                      />
                    )}
                    {scope.kind === "workspace" && item.status === "active" && (
                      <ConnectorCatalog provider={item} />
                    )}
                  </div>
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
}: {
  scope: ConnectorScope;
  providerId?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
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
      onOpenChange={setOpen}
      trigger={
        <Button
          size={providerId ? "sm" : "default"}
          variant={providerId ? "outline" : "default"}
          type="button"
        >
          {t(providerId ? "Manage" : "Add provider")}
        </Button>
      }
      size={"md"}
      title={t(providerId ? "Connector provider" : "Add connector provider")}
      description={t(
        "Provider credentials authenticate the integration service. External account authorization happens separately.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading />
        ) : definitions.error || resource.error ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
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
    [name, setName] = useState(initial?.name ?? ""),
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
      if (basis)
        return client.http
          .PATCH("/api/v1/connector-providers/{connector_provider_id}", {
            params: { path: { connector_provider_id: basis.id } },
            body: { name, expected_version: basis.version },
          })
          .then(data);
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
  const rotate = useMutation({
    mutationFn: () => {
      if (!basis || !definition) throw new Error(t("Provider unavailable."));
      validateSettings(definition.credential_schema, credentials);
      const body = {
        expected_version: basis.version,
        credentials: stringValues(credentials),
      };
      return client.http
        .POST(
          "/api/v1/connector-providers/{connector_provider_id}/credentials",
          {
            params: {
              path: { connector_provider_id: basis.id },
              header: commandHeaders(
                scope.kind === "workspace" ? scope.id : undefined,
                key.forBody(body),
              ),
            },
            body,
          },
        )
        .then(data);
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
    <div className={styles.stack}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
        {basis ? (
          <DisclosureSection title={<>{t("Configuration")}</>}>
            <JsonView value={configuration} />
          </DisclosureSection>
        ) : (
          <>
            <ChoiceField
              placeholder={t("Select provider type")}
              value={type}
              className="min-w-0"
              required
              onValueChange={(value) => {
                setType(value);
                setConfiguration({});
                setCredentials({});
              }}
              label={t("Provider type")}
              options={definitions.map((item) => ({
                value: item.type,
                label: item.display_name,
              }))}
            />
            {definition && (
              <>
                <SchemaFields
                  key={type}
                  schema={definition.configuration_schema}
                  value={configuration}
                  onChange={setConfiguration}
                />
                <SchemaFields
                  key={`${type}-credentials`}
                  schema={definition.credential_schema}
                  value={credentials}
                  onChange={setCredentials}
                  secret
                />
              </>
            )}
          </>
        )}
        <ErrorNotice
          error={save.error}
          retry={basis ? () => void reload() : undefined}
        />
        <FormActions pending={save.isPending} />
      </form>
      {basis && (
        <>
          <div className={styles.actions}>
            <Button
              variant="outline"
              loading={test.isPending}
              onClick={() => test.mutate()}
              type="button"
            >
              {t("Test connection")}
            </Button>
            <Confirm
              title={t(
                basis.status === "active"
                  ? "Disable provider"
                  : "Enable provider",
              )}
              description={t(
                "This changes eligibility for setup, discovery, and new agent calls.",
              )}
              trigger={t(basis.status === "active" ? "Disable" : "Enable")}
              action={async () => {
                const action = basis.status === "active" ? "disable" : "enable",
                  body = { expected_version: basis.version };
                await client.http.POST(
                  "/api/v1/connector-providers/{connector_provider_id}/{action}",
                  {
                    params: {
                      path: { connector_provider_id: basis.id, action },
                      header: commandHeaders(
                        scope.kind === "workspace" ? scope.id : undefined,
                        key.forBody({ action, ...body }),
                      ),
                    },
                    body,
                  },
                );
                done();
              }}
            />
          </div>
          <ErrorNotice error={test.error} retry={() => void reload()} />
          {test.data && <JsonView value={test.data} />}
          {definition && (
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault();
                rotate.mutate();
              }}
            >
              <h3>{t("Replace credentials")}</h3>
              <SchemaFields
                schema={definition.credential_schema}
                value={credentials}
                onChange={setCredentials}
                secret
              />
              <ErrorNotice error={rotate.error} retry={() => void reload()} />
              <FormActions
                pending={rotate.isPending}
                label={t("Replace credentials")}
              />
            </form>
          )}
        </>
      )}
    </div>
  );
}
