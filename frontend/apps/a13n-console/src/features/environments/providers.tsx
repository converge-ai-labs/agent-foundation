import { PageActions } from "../../shared/page-actions";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField, Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, Empty, Loading, StateBadge } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/form";
import { Table, Pagination, useCursor } from "../../shared/collection";
import { SchemaFields } from "../../shared/schema-fields";
import { jsonObject, validateSettings } from "../../shared/validation";
import { environmentApi, type EnvironmentScope } from "./api";
import styles from "../../shared/shared.module.css";

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
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor(),
    api = environmentApi(client, scope);
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
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <ProviderEditor scope={scope} />}</PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
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
                render: (item) => (
                  <StateBadge state={item.enabled ? "enabled" : "disabled"} />
                ),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    item.credential_configured
                      ? "Configured"
                      : "Not configured",
                  ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) =>
                  (item.workspace_id ? manage : organizationAdmin) && (
                    <ProviderEditor
                      scope={
                        item.workspace_id
                          ? { kind: "workspace", id: item.workspace_id }
                          : { kind: "organization", id: item.organization_id }
                      }
                      providerId={item.id}
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
            title={t("No environment providers")}
            description={t(
              "Add a provider before creating templates or registering external environments.",
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
  scope: EnvironmentScope;
  providerId?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0),
    definitions = useEnvironmentTypes();
  const query = useQuery({
    queryKey: [
      "environment-providers",
      scope.kind,
      scope.id,
      "detail",
      providerId,
    ],
    enabled: open && !!providerId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-providers/{resource_id}", {
          params: { path: { resource_id: providerId! } },
          signal,
        })
        .then(representation),
  });
  return (
    <Dialog
      title={t(
        providerId ? "Edit environment provider" : "Add environment provider",
      )}
      description={t(
        "The backend type and configuration are fixed after creation.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button
          size={providerId ? "sm" : "md"}
          variant={providerId ? "secondary" : "primary"}
        >
          {t(providerId ? "Edit" : "Add provider")}
        </Button>
      }
    >
      {open &&
        (definitions.isPending || (providerId && query.isPending) ? (
          <Loading />
        ) : definitions.error || query.error ? (
          <ErrorNotice error={definitions.error ?? query.error} />
        ) : (
          <ProviderForm
            key={generation}
            scope={scope}
            initial={providerId ? query.data : undefined}
            definitions={definitions.data?.items ?? []}
            close={() => setOpen(false)}
            reload={async () => {
              await query.refetch();
              setGeneration((value) => value + 1);
            }}
          />
        ))}
    </Dialog>
  );
}
function ProviderForm({
  scope,
  initial,
  definitions,
  close,
  reload,
}: {
  scope: EnvironmentScope;
  initial?: ReturnType<typeof representation<Schema["EnvironmentProvider"]>>;
  definitions: Record<string, unknown>[];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    [basis] = useState(initial),
    [name, setName] = useState(initial?.value.name ?? ""),
    [type, setType] = useState(initial?.value.type ?? ""),
    [enabled, setEnabled] = useState(initial?.value.enabled ?? true),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.value.configuration ?? {},
    ),
    [credential, setCredential] = useState<Record<string, unknown>>({});
  const definition = definitions.find((item) => item.type === type),
    configSchema = schema(definition?.configuration_schema),
    credentialSchema = schema(definition?.credential_schema);
  function done() {
    void cache.invalidateQueries({ queryKey: ["environment-providers"] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      if (basis)
        return client.http
          .PATCH("/api/v1/environment-providers/{provider_id}", {
            params: {
              path: { provider_id: basis.value.id },
              header: { "If-Match": basis.etag ?? "" },
            },
            body: { name, enabled },
          })
          .then(data);
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
  const rotate = useMutation({
    mutationFn: async (remove: boolean) => {
      if (!basis) return;
      if (!remove) validateSettings(credentialSchema, credential);
      return client.http
        .PUT("/api/v1/environment-providers/{provider_id}/credential", {
          params: {
            path: { provider_id: basis.value.id },
            header: { "If-Match": basis.etag ?? "" },
          },
          body: {
            credential: remove ? null : jsonObject(JSON.stringify(credential)),
          },
        })
        .then(data);
    },
    onSuccess: done,
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
        <Input
          label={t("Name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={128}
        />
        {basis ? (
          <>
            <Switch
              label={t("Enabled")}
              checked={enabled}
              onCheckedChange={setEnabled}
            />
            <details>
              <summary>{t("Configuration")}</summary>
              <JsonView value={configuration} />
            </details>
          </>
        ) : (
          <>
            <SelectField
              label={t("Provider type")}
              placeholder={t("Select provider type")}
              value={type}
              required
              onValueChange={(value) => {
                setType(value);
                setConfiguration({});
                setCredential({});
              }}
              options={definitions.flatMap((item) =>
                typeof item.type === "string"
                  ? [{ value: item.type, label: item.type }]
                  : [],
              )}
            />
            <SchemaFields
              key={type}
              schema={configSchema}
              value={configuration}
              onChange={setConfiguration}
            />
            <SchemaFields
              key={`${type}-credential`}
              schema={credentialSchema}
              value={credential}
              onChange={setCredential}
              secret
            />
          </>
        )}
        <ErrorNotice
          error={save.error}
          retry={basis ? () => void reload() : undefined}
        />
        <FormActions pending={save.isPending} />
      </form>
      {basis && Object.keys(credentialSchema).length > 0 && (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            rotate.mutate(false);
          }}
        >
          <h3>{t("Replace credentials")}</h3>
          <p className={styles.muted}>
            {t(
              "Existing credentials are never displayed. Supply a complete replacement.",
            )}
          </p>
          <SchemaFields
            schema={credentialSchema}
            value={credential}
            onChange={setCredential}
            secret
          />
          <ErrorNotice error={rotate.error} retry={() => void reload()} />
          <div className={styles.actions}>
            <Button type="submit" variant="primary" loading={rotate.isPending}>
              {t("Replace credentials")}
            </Button>
            {basis.value.credential_configured && (
              <Button
                variant="danger"
                onClick={() => rotate.mutate(true)}
                loading={rotate.isPending}
              >
                {t("Remove credentials")}
              </Button>
            )}
          </div>
        </form>
      )}
    </div>
  );
}
