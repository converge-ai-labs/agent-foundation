import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import {
  CatalogStep,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import { useCredentialSection } from "../../shared/use-credential-section";
import { AddProviderDialog } from "./add-provider-dialog";
import type { ProviderCategoryValue } from "./categories";
import { ConnectionTest, providerTestResult } from "./connection-test";
import {
  credentialDescription,
  credentialHint,
  credentialLabel,
} from "./credential-hint";
import { CredentialRow } from "./credential-row";
import { EditProviderDialog } from "./edit-provider-dialog";
import { ProviderConnectFields } from "./connect-fields";
import { providerKeyLink } from "./key-urls";
import { ProviderEditor, ProviderGroup, ProviderName } from "./provider-editor";
import { ProviderFacts } from "./provider-facts";
import { ProviderTable } from "./provider-table";
import providerStyles from "./providers.module.css";
import { providerApi, type ProviderKind, type ProviderScope } from "./api";

type Definition = Schema["ProviderType"];
type Resource = { value: Schema["Provider"]; etag?: string };

const categories: Record<ProviderKind, ProviderCategoryValue> = {
  environment: "environments",
  memory: "memory",
};

/** The types a deployment offers for one provider kind. */
export function useProviderTypes(kind: ProviderKind, enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: [`${kind}-provider-types`],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind } },
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

/**
 * The providers of a kind whose configuration is chosen when the provider is
 * added and shown afterwards; the editor changes the name, enabled state and
 * credential, and tests the saved account when its type can.
 */
export function KindProviders({
  kind,
  scope,
  notice,
  addDescription,
  testDescription,
}: {
  kind: ProviderKind;
  scope: ProviderScope;
  notice?: ReactNode;
  /** What the catalog step asks the reader to choose, as a translation key. */
  addDescription: string;
  /** What testing a saved provider does, as a translation key. */
  testDescription: string;
}) {
  const providerTypes = useProviderTypes(kind);
  const client = useClient(),
    { can, organizationCan, organization } = useAccess(),
    page = useCursor(),
    api = providerApi(client, organization.id, scope, kind);
  const rows = useResourceRows<Schema["Provider"]>();
  const query = useQuery({
    queryKey: [`${kind}-providers`, scope.kind, scope.id, "list", page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization" ? organizationCan("write") : can("write");
  const connectable = providerTypes.data?.items ?? [];
  const add =
    manage && connectable.length ? (
      <AddKindProvider
        kind={kind}
        scope={scope}
        definitions={connectable}
        description={addDescription}
      />
    ) : undefined;
  const definitionFor = (type: string) =>
    providerTypes.data?.items.find((entry) => entry.type === type);
  return (
    <>
      {rows.selected && (
        <EditKindProvider
          key={rows.selected.id}
          kind={kind}
          scope={
            rows.selected.workspace_id
              ? { kind: "workspace", id: rows.selected.workspace_id }
              : { kind: "organization", id: rows.selected.organization_id }
          }
          provider={rows.selected}
          testDescription={testDescription}
          {...rows.control}
        />
      )}
      <ProviderTable
        category={categories[kind]}
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        canActivateRow={(item) =>
          item.workspace_id ? manage : organizationCan("write")
        }
        onRowActivate={rows.activate}
        notice={notice}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitionFor(item.type)?.display_name ?? item.type,
          workspaceId: item.workspace_id,
          credentials:
            definitionFor(item.type)?.credential_schema == null
              ? ("not_required" as const)
              : item.credential_configured
                ? ("configured" as const)
                : ("not_configured" as const),
          state: item.enabled ? "enabled" : "disabled",
        })}
      />
    </>
  );
}

/** Catalog-first creation for the types the deployment offers. */
function AddKindProvider({
  kind,
  scope,
  definitions,
  description,
}: {
  kind: ProviderKind;
  scope: ProviderScope;
  definitions: Definition[];
  description: string;
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
      description={t(description)}
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
            kind={kind}
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

function EditKindProvider({
  kind,
  scope,
  provider,
  testDescription,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  kind: ProviderKind;
  scope: ProviderScope;
  provider: Schema["Provider"];
  testDescription: string;
}) {
  const client = useClient(),
    [generation, setGeneration] = useState(0),
    definitions = useProviderTypes(kind);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const query = useQuery({
    queryKey: [
      `${kind}-providers`,
      scope.kind,
      scope.id,
      "detail",
      provider.id,
    ],
    enabled: state.open,
    queryFn: ({ signal }) =>
      providerApi(client, provider.organization_id, scope, kind).provider(
        provider.id,
        signal,
      ),
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
      loading={definitions.isPending || query.isPending}
      error={definitions.error ?? query.error}
    >
      {query.data && (
        <ProviderForm
          key={generation}
          kind={kind}
          scope={scope}
          initial={query.data}
          definitions={definitions.data?.items ?? []}
          testDescription={testDescription}
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
  kind,
  scope,
  initial,
  definition: chosen,
  definitions,
  testDescription,
  close,
  reload,
}: {
  kind: ProviderKind;
  scope: ProviderScope;
  initial?: Resource;
  definition?: Definition;
  definitions: Definition[];
  /** What testing the saved provider does; creation offers no test. */
  testDescription?: string;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    { organization } = useAccess(),
    [basis] = useState(initial),
    [name, setName] = useState(
      initial?.value.name ?? chosen?.display_name ?? "",
    ),
    type = initial?.value.type ?? chosen?.type ?? "",
    [enabled, setEnabled] = useState(initial?.value.enabled ?? true),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.value.config ?? {},
    ),
    [advancedOpen, setAdvancedOpen] = useState(false);
  const api = providerApi(
      client,
      basis?.value.organization_id ?? organization.id,
      scope,
      kind,
    ),
    definition = definitions.find((item) => item.type === type) ?? chosen,
    configSchema = schema(definition?.configuration_schema),
    section = useCredentialSection(definition, configuration, basis?.value);
  function done() {
    void cache.invalidateQueries({ queryKey: [`${kind}-providers`] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      const credential = section.payload();
      if (credential) validateSettings(section.schema, credential);
      if (basis) {
        return api.updateProvider(basis.value.id, basis.etag, {
          name,
          enabled,
          ...(credential === undefined
            ? {}
            : {
                credential:
                  credential === null
                    ? null
                    : jsonObject(JSON.stringify(credential)),
              }),
        });
      }
      validateSettings(configSchema, configuration);
      return api.createProvider({
        name,
        type,
        config: jsonObject(JSON.stringify(configuration)),
        ...(credential
          ? { credential: jsonObject(JSON.stringify(credential)) }
          : {}),
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
          credentialSchema={
            section.mode === "forbidden" ? undefined : section.schema
          }
          configurationSchema={configSchema}
          credential={section.credential}
          onCredentialChange={section.setCredential}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyLink(definition)}
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
        {section.visible && (
          <CredentialRow
            label={t(credentialLabel(section.schema))}
            configured={section.removable}
            removing={section.removing}
            onRemovingChange={section.setRemoving}
            onDiscard={() => section.setCredential({})}
          >
            {section.mode !== "forbidden" && (
              <SchemaFields
                secret
                autoFocus
                labelAction={
                  providerKeyLink(definition) && (
                    <ProviderKeyLink {...providerKeyLink(definition)!} />
                  )
                }
                schema={section.schema}
                requireFields={section.requireFields}
                value={section.credential}
                onChange={section.setCredential}
              />
            )}
          </CredentialRow>
        )}
        {definition?.supports_test && testDescription && (
          <ConnectionTest
            action={async () =>
              providerTestResult(await api.testProvider(basis.value.id))
            }
            description={testDescription}
            dirty={
              save.isPending ||
              name !== basis.value.name ||
              enabled !== basis.value.enabled ||
              Object.keys(section.credential).length > 0 ||
              section.removing
            }
            retry={() => void reload()}
          />
        )}
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
