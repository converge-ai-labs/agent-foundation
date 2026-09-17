import { useSuggestedName } from "../../shared/suggested-name";
import { FormSection, formSectionStyles } from "../../shared/form-section";
import { CredentialEditor } from "../../shared/credential-editor";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import { ResourceReference } from "../../shared/resource-reference";
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
  ModalFrame,
  ReadOnlyField,
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
import { FormActions } from "../../shared/form";
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
      <p>
        {t(
          "Direct Local and Docker require a single-host deployment and operator configuration.",
        )}
      </p>
      <PageActions>
        {manage &&
          providerTypes.data?.items.some(
            (type) => !type.deployment_managed,
          ) && <ProviderEditor scope={scope} />}
      </PageActions>
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
        <Loading variant="table" columns={4} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            canActivateRow={(item) =>
              item.configuration_source === "deployment" ||
              (item.workspace_id ? manage : organizationAdmin)
            }
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Provider"),
                tone: "primary",
                render: (item) => (
                  <div className="flex min-w-0 items-center gap-3">
                    <ProviderIcon key={item.type} type={item.type} />
                    <ResourceIdentity
                      name={item.name}
                      resourceId={item.id}
                      description={
                        item.configuration_source === "deployment"
                          ? t("Configured by deployment")
                          : String(
                              providerTypes.data?.items.find(
                                (entry) => entry.type === item.type,
                              )?.display_name ?? item.type,
                            )
                      }
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
                label: t("Credentials"),
                render: (item) =>
                  t(
                    providerTypes.data?.items.find(
                      (entry) => entry.type === item.type,
                    )?.credential_schema == null
                      ? "Not required"
                      : item.credential_configured
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
      size="lg"
      title={t(
        providerId
          ? query.data?.value.configuration_source === "deployment"
            ? "Provider details"
            : "Edit provider"
          : "Add provider",
      )}
      description={
        providerId ? undefined : t("Choose where your environments run.")
      }
      closeLabel={t("Close")}
    >
      {open &&
        (definitions.isPending || (providerId && query.isPending) ? (
          <Loading variant="form" rows={4} />
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
    { name, setName, suggestName } = useSuggestedName(initial?.value.name),
    [type, setType] = useState(initial?.value.type ?? ""),
    [enabled, setEnabled] = useState(initial?.value.enabled ?? true),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.value.configuration ?? {},
    ),
    [removeCredential, setRemoveCredential] = useState(false),
    [credential, setCredential] = useState<Record<string, unknown>>({});
  const deployment = basis?.value.configuration_source === "deployment";
  const definition = definitions.find((item) => item.type === type),
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
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (!deployment) save.mutate();
      }}
    >
      <FormSection>
        {deployment ? (
          <ReadOnlyField label={t("Name")}>
            <span className="flex items-center gap-2">
              {name}
              {basis && <ResourceReference id={basis.value.id} />}
            </span>
          </ReadOnlyField>
        ) : (
          <FormField
            className="min-w-0 w-full"
            label={t("Name")}
            labelAction={basis && <ResourceReference id={basis.value.id} />}
          >
            <Input
              required={true}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
          </FormField>
        )}
      </FormSection>
      <FormSection
        title={t("Connection")}
        description={
          deployment
            ? t(
                "Connection settings come from the running Service and cannot be edited here.",
              )
            : undefined
        }
      >
        <ProviderTypeField
          definitions={
            basis
              ? definitions
              : definitions.filter((item) => !item.deployment_managed)
          }
          value={type}
          readOnly={!!basis}
          onValueChange={(value) => {
            setType(value);
            suggestName(
              definitions.find((item) => item.type === value)?.display_name ??
                value,
            );
            setConfiguration({});
            setCredential({});
          }}
          labelAction={
            type === "e2b" && (
              <ProviderKeyLink href="https://e2b.dev/dashboard?tab=keys" />
            )
          }
        />
        {basis?.value.type === "docker" && (
          <div className={styles.stack}>
            <p>
              {t("Enabled: {{value}}", {
                value: basis.value.enabled ? t("Yes") : t("No"),
              })}
            </p>
            <p role="status">
              {t("Engine: {{status}}", {
                status: t(connectivity.data?.status ?? "unknown"),
              })}
            </p>
            {connectivity.data?.error && <p>{connectivity.data.error}</p>}
          </div>
        )}
        {basis && Object.keys(configuration).length > 0 && (
          <DisclosureSection title={t("Configuration details")}>
            <ConfigurationSummary value={configuration} schema={configSchema} />
          </DisclosureSection>
        )}
        {!basis && (
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
      </FormSection>
      {basis && !!Object.keys(schema(credentialSchema.properties)).length && (
        <FormSection title={t("Credentials")}>
          <CredentialEditor
            configured={basis.value.credential_configured}
            removing={removeCredential}
            onRemovingChange={(value) => {
              setRemoveCredential(value);
              setCredential({});
            }}
          >
            <SchemaFields
              secret
              schema={{ ...credentialSchema, required: [] }}
              value={credential}
              onChange={setCredential}
            />
          </CredentialEditor>
        </FormSection>
      )}
      {!deployment && basis && (
        <FormSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        </FormSection>
      )}
      <ErrorNotice
        error={save.error}
        retry={basis ? () => void reload() : undefined}
      />
      {deployment ? (
        <footer data-a13n-form-actions className={styles.formActions}>
          <Button type="button" variant="outline" onClick={close}>
            {t("Close")}
          </Button>
        </footer>
      ) : (
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={t(basis ? "Save changes" : "Add provider")}
        />
      )}
    </form>
  );
}
