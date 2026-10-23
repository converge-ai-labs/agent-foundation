import { PlusIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
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
import { VercelChoices } from "./vercel-choices";
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
import {
  ProviderConnectFields,
  ProviderConfigurationFields,
} from "./connect-fields";
import { EnvironmentConnectionDelete } from "./environment-connection-delete";
import { providerKeyLink } from "./key-urls";
import {
  ProviderEditor,
  ProviderGroup,
  ProviderName,
  ProviderReadOnly,
} from "./provider-editor";
import { ProviderFacts } from "./provider-facts";
import { ProviderTable } from "./provider-table";
import providerStyles from "./providers.module.css";
import { providerApi, type ProviderKind } from "./api";

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

/** Workspace providers; environment connections also support configuration edits and safe deletion. */
export function KindProviders({
  kind,
  notice,
  addDescription,
  testDescription,
  renderCatalog,
}: {
  kind: ProviderKind;
  renderCatalog?: (
    definitions: readonly Definition[],
    onChoose: (type: string) => void,
  ) => ReactNode;
  notice?: ReactNode;
  /** What the catalog step asks the reader to choose, as a translation key. */
  addDescription: string;
  /** What testing a saved provider does, as a translation key. */
  testDescription: string;
}) {
  const providerTypes = useProviderTypes(kind);
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    page = useCursor(),
    api = providerApi(client, workspace.id, kind);
  const rows = useResourceRows<Schema["Provider"]>();
  const query = useQuery({
    queryKey: [`${kind}-providers`, workspace.id, "list", page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const manage = can("write");
  const connectable = providerTypes.data?.items ?? [];
  const add =
    manage && connectable.length ? (
      <AddKindProvider
        kind={kind}
        definitions={connectable}
        description={addDescription}
        renderCatalog={renderCatalog}
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
        rowActionLabel={manage ? "Edit" : "View"}
        canActivateRow={() => kind === "environment" || manage}
        onRowActivate={rows.activate}
        notice={notice}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitionFor(item.type)?.display_name ?? item.type,
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
  definitions,
  description,
  renderCatalog,
}: {
  renderCatalog?: (
    definitions: readonly Definition[],
    onChoose: (type: string) => void,
  ) => ReactNode;
  kind: ProviderKind;
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
      title={kind === "environment" ? t("Add connection") : undefined}
      trigger={
        kind === "environment" ? (
          <Button type="button">
            <PlusIcon aria-hidden="true" />
            {t("Add connection")}
          </Button>
        ) : undefined
      }
      definitions={definitions}
      renderCatalog={renderCatalog}
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
            createLabel={
              kind === "environment" ? t("Add connection") : undefined
            }
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
  provider,
  testDescription,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  kind: ProviderKind;
  provider: Schema["Provider"];
  testDescription: string;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    [generation, setGeneration] = useState(0),
    definitions = useProviderTypes(kind);
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const query = useQuery({
    queryKey: [`${kind}-providers`, workspace.id, "detail", provider.id],
    enabled: state.open,
    queryFn: ({ signal }) =>
      providerApi(client, workspace.id, kind).provider(provider.id, signal),
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
      readOnly={!can("write")}
      type={provider.type}
      definition={definition?.display_name}
      loading={definitions.isPending || query.isPending}
      error={definitions.error ?? query.error}
    >
      {query.data && !can("write") ? (
        <ProviderReadOnly
          enabled={query.data.value.enabled}
          credentials={
            query.data.value.credential_configured
              ? "configured"
              : "not_configured"
          }
          configuration={query.data.value.config}
          schema={definition?.configuration_schema}
          onClose={() => state.setOpen(false)}
        />
      ) : (
        query.data && (
          <ProviderForm
            key={generation}
            kind={kind}
            initial={query.data}
            definitions={definitions.data?.items ?? []}
            testDescription={testDescription}
            close={() => state.setOpen(false)}
            reload={async () => {
              await query.refetch();
              setGeneration((value) => value + 1);
            }}
          />
        )
      )}
    </EditProviderDialog>
  );
}

export function ProviderForm({
  kind,
  initial,
  definition: chosen,
  createLabel,
  definitions,
  testDescription,
  close,
  reload,
}: {
  kind: ProviderKind;
  initial?: Resource;
  createLabel?: string;
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
    { workspace } = useWorkspace(),
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
  const api = providerApi(client, workspace.id, kind),
    definition = definitions.find((item) => item.type === type) ?? chosen,
    configSchema = schema(definition?.configuration_schema),
    section = useCredentialSection(definition, configuration, basis?.value);
  const configChanged =
    kind === "environment" &&
    !!basis &&
    JSON.stringify(configuration) !== JSON.stringify(basis.value.config);
  function done() {
    void cache.invalidateQueries({ queryKey: [`${kind}-providers`] });
    if (kind === "environment")
      void cache.invalidateQueries({
        queryKey: ["environment-provider-options"],
      });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      const credential = section.payload();
      if (credential) validateSettings(section.schema, credential);
      if (basis) {
        if (configChanged) validateSettings(configSchema, configuration);
        return api.updateProvider(basis.value.id, basis.etag, {
          name,
          enabled,
          ...(configChanged
            ? { config: jsonObject(JSON.stringify(configuration)) }
            : {}),
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
          event.stopPropagation();
          save.mutate();
        }}
      >
        <ProviderConnectFields
          credentialSchema={
            section.mode === "forbidden" ? undefined : section.schema
          }
          configurationSchema={configSchema}
          credential={section.credential}
          onCredentialChange={(next) => {
            section.setCredential(next);
            if (kind === "environment" && type === "vercel")
              setConfiguration({});
          }}
          configurationFields={
            kind === "environment" && type === "vercel" ? (
              <VercelChoices
                credential={section.credential}
                value={configuration}
                onChange={setConfiguration}
              />
            ) : undefined
          }
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          nameDescription={
            kind === "environment"
              ? "How this connection is listed across the console."
              : undefined
          }
          onNameChange={setName}
          keyLink={providerKeyLink(definition)}
          advancedOpen={advancedOpen}
          onAdvancedOpenChange={setAdvancedOpen}
        />
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={createLabel ?? t("Add provider")}
        />
      </form>
    );
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        save.mutate();
      }}
    >
      <ProviderName
        value={name}
        onChange={setName}
        description={
          kind === "environment"
            ? "How this connection is listed across the console."
            : undefined
        }
      />
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
              configChanged ||
              name !== basis.value.name ||
              enabled !== basis.value.enabled ||
              Object.keys(section.credential).length > 0 ||
              section.removing
            }
            retry={() => void reload()}
          />
        )}
        {kind === "environment" ? (
          <ProviderConfigurationFields
            schema={configSchema}
            value={configuration}
            onChange={setConfiguration}
            advancedOpen={advancedOpen}
            onAdvancedOpenChange={setAdvancedOpen}
          />
        ) : (
          <ProviderFacts
            hideDefaults
            configuration={configuration}
            schema={configSchema}
          />
        )}
      </ProviderGroup>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save changes")}
        leading={
          kind === "environment" ? (
            <EnvironmentConnectionDelete
              provider={basis.value}
              etag={basis.etag}
              disabled={save.isPending}
              onDeleted={close}
            />
          ) : undefined
        }
      />
    </ProviderEditor>
  );
}
