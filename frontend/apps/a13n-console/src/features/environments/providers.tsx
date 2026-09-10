import { ProviderTypeField } from "../../shared/provider-type-field";
import { ProviderEnabled } from "../../shared/provider-enabled";
import {
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ProviderIcon } from "../../shared/provider-icon";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/scope-badge";
import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  SettingsSection,
  SettingsRow,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";
import { environmentApi, type EnvironmentScope } from "./api";

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
              item.workspace_id ? manage : organizationAdmin
            }
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Provider"),
                render: (item) => (
                  <div className="flex min-w-0 items-center gap-3">
                    <ProviderIcon key={item.type} type={item.type} />
                    <ResourceIdentity
                      name={item.name}
                      description={String(
                        providerTypes.data?.items.find(
                          (entry) => entry.type === item.type,
                        )?.display_name ?? item.type,
                      )}
                    />
                  </div>
                ),
              },
              {
                label: t("Scope"),
                render: (item) => (
                  <ScopeBadge workspaceId={item.workspace_id} />
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
                label: t("Status"),
                render: (item) => (
                  <StateBadge state={item.enabled ? "enabled" : "disabled"} />
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
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  scope: EnvironmentScope;
  providerId?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    definitions = useEnvironmentTypes();
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });

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
      size={"md"}
      title={t(providerId ? "Edit provider" : "Add provider")}
      description={t(
        "Credentials are stored securely and never returned by the service.",
      )}
      closeLabel={t("Close")}
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
  scope: EnvironmentScope;
  initial?: ReturnType<typeof representation<Schema["EnvironmentProvider"]>>;
  definitions: Schema["EnvironmentProviderDefinition"][];
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
    [removeCredential, setRemoveCredential] = useState(false),
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
        <ProviderTypeField
          definitions={definitions}
          value={type}
          disabled={!!basis}
          onValueChange={(value) => {
            setType(value);
            setConfiguration({});
            setCredential({});
          }}
          labelAction={
            type === "a13n.e2b" && (
              <ProviderKeyLink href="https://e2b.dev/dashboard?tab=keys" />
            )
          }
        />
        {basis ? (
          <>
            {Object.keys(configuration).length > 0 && (
              <DisclosureSection title={t("Configuration")}>
                <JsonView value={configuration} />
              </DisclosureSection>
            )}
          </>
        ) : (
          <>
            <SchemaFields
              key={type}
              schema={configSchema}
              value={configuration}
              onChange={setConfiguration}
            />
            <SchemaFields
              secret
              key={`${type}-credential`}
              schema={credentialSchema}
              value={credential}
              onChange={setCredential}
            />
          </>
        )}
        {basis && (
          <>
            {!!Object.keys(schema(credentialSchema.properties)).length &&
              !removeCredential && (
                <>
                  <p className={styles.muted}>
                    {t("Leave empty to keep the current credential.")}
                  </p>
                  <SchemaFields
                    secret
                    schema={{ ...credentialSchema, required: [] }}
                    value={credential}
                    onChange={setCredential}
                  />
                </>
              )}
            <SettingsSection>
              <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
              {basis.value.credential_configured && (
                <SettingsRow
                  stackOnNarrow={false}
                  label={t("Saved credentials")}
                  description={t(
                    removeCredential
                      ? "Credentials will be removed when you save."
                      : "Replace them above, or remove the saved credentials.",
                  )}
                >
                  <Button
                    variant="ghost"
                    size="sm"
                    className={
                      removeCredential ? undefined : "text-destructive"
                    }
                    onClick={() => setRemoveCredential(!removeCredential)}
                  >
                    {t(removeCredential ? "Undo" : "Remove")}
                  </Button>
                </SettingsRow>
              )}
            </SettingsSection>
          </>
        )}
        <ErrorNotice
          error={save.error}
          retry={basis ? () => void reload() : undefined}
        />
        <FormActions pending={save.isPending} onCancel={close} />
      </form>
    </div>
  );
}
