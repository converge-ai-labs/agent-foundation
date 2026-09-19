import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { ApiError } from "../../service-client";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ListRow, ListRows } from "../../shared/collection";
import {
  CatalogStep,
  ConflictNotice,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { credentialMode } from "../../shared/provider-authentication";
import { useCredentialSection } from "../../shared/use-credential-section";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  jsonObject,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import {
  AddProviderDialog,
  CredentialRow,
  EditProviderDialog,
  ProviderConnectFields,
  ProviderEditor,
  ProviderEditorFields,
  ProviderFacts,
  ProviderGroup,
  ProviderName,
  ProviderReadOnly,
  credentialDescription,
  credentialHint,
  credentialLabel,
  providerKeyLink,
  providerStyles,
} from "../providers";
import { useMemoryProviderDefinitions } from "./availability";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";

type Definition = Schema["MemoryProviderMetadata"];

/** The target a saved backend cannot be pointed away from. */
const storageNote =
  "Storage configuration cannot be changed. Create a new provider for a different target; existing memories are not migrated.";

/** Whether a backend asks for a secret at all, as its definition declares. */
export function memoryCredentialState(
  provider: {
    configuration: Record<string, unknown>;
    credential_configured?: boolean;
  },
  definition?: Definition,
) {
  if (
    definition &&
    credentialMode(definition, provider.configuration) !== "required"
  )
    return "not_required" as const;
  return provider.credential_configured
    ? ("configured" as const)
    : ("not_configured" as const);
}

/**
 * Creation and editing share one entry point so callers that only know they
 * want "a memory provider editor" keep working.
 */
export function MemoryProviderEditor({
  providerId,
  ...props
}: ResourceEditorControl & {
  scope: MemoryProviderScope;
  providerId?: string;
  extra?: ReactNode;
  readOnly?: boolean;
  onSaved?: (provider: Schema["MemoryProvider"]) => void;
}) {
  return providerId ? (
    <EditMemoryProvider providerId={providerId} {...props} />
  ) : (
    <AddMemoryProvider {...props} />
  );
}

/** Catalog-first creation: choose the backend, then configure it. */
export function AddMemoryProvider({
  scope,
  controlledOpen,
  onClose,
  finalFocus,
  onSaved,
}: ResourceEditorControl & {
  scope: MemoryProviderScope;
  onSaved?: (provider: Schema["MemoryProvider"]) => void;
}) {
  const { t } = useTranslation();
  const [localOpen, setLocalOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const open = controlledOpen ?? localOpen;
  const definitions = useMemoryProviderDefinitions(scope, open);
  // Closing discards the step and the draft, whoever asked for it.
  function change(value: boolean) {
    setLocalOpen(value);
    if (!value) {
      onClose?.();
      setGeneration((current) => current + 1);
    }
  }
  return (
    <AddProviderDialog<Definition>
      key={generation}
      definitions={definitions.data?.items}
      error={definitions.error}
      open={open}
      finalFocus={finalFocus}
      trigger={controlledOpen === undefined ? undefined : null}
      onOpenChange={change}
      description={t("Choose the backend that stores your agents' memories.")}
      hint={(definition) => credentialHint(definition.credential_schema)}
      connectDescription={(definition) =>
        t(credentialDescription(definition.credential_schema), {
          provider: definition.display_name,
        })
      }
    >
      {(definition, back) => (
        <CatalogStep backLabel={t("All providers")} onBack={back}>
          <MemoryProviderForm
            scope={scope}
            definition={definition}
            definitions={definitions.data?.items ?? []}
            onCancel={() => change(false)}
            onSaved={(provider) => {
              change(false);
              onSaved?.(provider);
            }}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

function EditMemoryProvider({
  scope,
  providerId,
  controlledOpen,
  onClose,
  finalFocus,
  extra,
  readOnly = false,
  onSaved,
}: ResourceEditorControl & {
  scope: MemoryProviderScope;
  providerId: string;
  extra?: ReactNode;
  readOnly?: boolean;
  onSaved?: (provider: Schema["MemoryProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const [pending, setPending] = useState(false);
  const definitions = useMemoryProviderDefinitions(scope, state.open);
  const resource = useQuery({
    queryKey: ["memory-provider", scope.kind, scope.id, providerId],
    enabled: state.open,
    queryFn: ({ signal }) =>
      memoryProviderApi(client, scope).provider(providerId, signal),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === resource.data?.value.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      onOpenChange={(open, details) => {
        if (pending) details.cancel();
        else state.modalProps.onOpenChange(open, details);
      }}
      open={state.open}
      name={resource.data?.value.name}
      id={providerId}
      type={resource.data?.value.type}
      definition={definition?.display_name}
      scope={resource.data?.value.workspace_id ? "workspace" : "organization"}
      readOnly={readOnly}
      loading={definitions.isPending || resource.isPending}
      error={resource.error}
    >
      {resource.data &&
        (readOnly ? (
          <ProviderReadOnly
            enabled={resource.data.value.enabled}
            credentials={memoryCredentialState(resource.data.value, definition)}
            configuration={resource.data.value.configuration}
            schema={definition?.configuration_schema}
            note={t(storageNote)}
            leading={extra}
            onClose={() => state.setOpen(false)}
          />
        ) : (
          <MemoryProviderForm
            scope={scope}
            resource={resource.data}
            definitions={definitions.data?.items ?? []}
            extra={extra}
            onPending={setPending}
            onCancel={() => state.setOpen(false)}
            onSaved={(provider) => {
              state.setOpen(false);
              onSaved?.(provider);
            }}
          />
        ))}
    </EditProviderDialog>
  );
}

export function MemoryProviderForm({
  scope,
  resource,
  definition: chosen,
  definitions,
  extra,
  onCancel,
  onSaved,
  onPending,
}: {
  scope: MemoryProviderScope;
  resource?: { value: Schema["MemoryProvider"]; etag?: string };
  /** Fixed by the catalog when creating. */
  definition?: Definition;
  definitions: Definition[];
  extra?: ReactNode;
  onCancel: () => void;
  onSaved: (provider: Schema["MemoryProvider"]) => void;
  onPending?: (pending: boolean) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [original, setOriginal] = useState(resource);
  const type =
    resource?.value.type ?? chosen?.type ?? definitions[0]?.type ?? "";
  const [name, setName] = useState(
    resource?.value.name ??
      chosen?.display_name ??
      definitions[0]?.display_name ??
      "",
  );
  const [configuration, setConfiguration] = useState<Record<string, unknown>>(
    resource?.value.configuration ?? {},
  );
  const [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [error, setError] = useState<unknown>();
  const [uncertainCreate, setUncertainCreate] = useState(false);
  const [existing, setExisting] = useState<Schema["MemoryProvider"][]>();
  const api = memoryProviderApi(client, scope);
  const definition = definitions.find((item) => item.type === type) ?? chosen;
  const configSchema = definition?.configuration_schema ?? {};
  const section = useCredentialSection(
    definition,
    configuration,
    original?.value,
  );
  const save = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      if (!name.trim()) throw new Error(t("Enter a provider name."));
      const credentials = section.payload();
      if (credentials) validateSettings(section.schema, credentials);
      if (!original) {
        if (!definition) throw new Error(t("Choose a provider type."));
        const config = withSchemaValues(configSchema, configuration);
        validateSettings(configSchema, config);
        try {
          return await api.createProvider({
            type,
            name,
            enabled,
            configuration: jsonObject(JSON.stringify(config)),
            credential: credentials
              ? jsonObject(JSON.stringify(credentials))
              : null,
          });
        } catch (error) {
          if (
            !(error instanceof ApiError) ||
            error.status >= 500 ||
            error.code === "memory_provider_name_conflict"
          )
            setUncertainCreate(true);
          throw error;
        }
      }
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, {
        name,
        enabled,
        ...(credentials === undefined
          ? {}
          : {
              credential:
                credentials === null
                  ? null
                  : jsonObject(JSON.stringify(credentials)),
            }),
      });
    },
    onSuccess: (provider) => {
      section.setCredential({});
      void cache.invalidateQueries({ queryKey: ["memory-providers"] });
      void cache.invalidateQueries({ queryKey: ["memory-provider"] });
      onSaved(provider);
    },
    onSettled: () => onPending?.(false),
  });
  const reconcile = useMutation({
    retry: false,
    mutationFn: () =>
      allPages((cursor) => api.providers(new AbortController().signal, cursor)),
    onSuccess: (items) =>
      setExisting(
        items.filter((item) =>
          scope.kind === "organization"
            ? item.workspace_id === null
            : item.workspace_id === scope.id,
        ),
      ),
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!save.isPending && !uncertainCreate) {
      onPending?.(true);
      save.mutate();
    }
  }
  const notices = (
    <>
      <ErrorNotice error={error ?? (conflict ? undefined : save.error)} />
      {conflict && original && (
        <ConflictNotice
          title={t("This provider changed")}
          description={t(
            "Your draft is preserved. Load the saved version, then save again.",
          )}
          recover={{
            label: t("Load current version and keep my draft"),
            onClick: async () => {
              try {
                setOriginal(
                  await api.provider(
                    original.value.id,
                    new AbortController().signal,
                  ),
                );
                save.reset();
                setError(undefined);
              } catch (error) {
                setError(error);
              }
            },
          }}
        />
      )}
      {uncertainCreate && (
        <ConflictNotice
          title={t("Creation could not be confirmed")}
          description={t(
            "Review saved providers before creating another; credentials cannot be compared.",
          )}
          recover={{
            label: t("Review existing providers"),
            onClick: () => reconcile.mutate(),
            pending: reconcile.isPending,
          }}
          proceed={
            existing
              ? {
                  label: t("I checked; allow another create attempt"),
                  onClick: () => {
                    setUncertainCreate(false);
                    setExisting(undefined);
                    reconcile.reset();
                    save.reset();
                  },
                }
              : undefined
          }
        >
          {existing && (
            <ListRows className="mt-2">
              {existing.map((item) => (
                <ListRow
                  key={item.id}
                  icon={<ProviderIcon type={item.type} />}
                  name={item.name}
                  secondary={
                    definitions.find((entry) => entry.type === item.type)
                      ?.display_name ?? item.type
                  }
                  actions={
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => onSaved(item)}
                    >
                      {t("Return to providers")}
                    </Button>
                  }
                />
              ))}
              {!existing.length && (
                <p className={styles.muted}>
                  {t("No saved providers found in this scope.")}
                </p>
              )}
            </ListRows>
          )}
        </ConflictNotice>
      )}
      <ErrorNotice error={reconcile.error} />
    </>
  );
  if (!original)
    return (
      <form className={providerStyles.connectForm} onSubmit={submit}>
        <fieldset
          disabled={save.isPending}
          className={providerStyles.connectFields}
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
        </fieldset>
        {extra}
        {notices}
        <FormActions
          onCancel={onCancel}
          label={t("Add provider")}
          pending={save.isPending}
          disabled={uncertainCreate}
        />
      </form>
    );
  return (
    <ProviderEditor onSubmit={submit}>
      <ProviderEditorFields disabled={save.isPending}>
        <ProviderName value={name} onChange={setName} />
        <ProviderGroup note={t(storageNote)}>
          <ProviderEnabled
            checked={enabled}
            onCheckedChange={setEnabled}
            description={t("Stored records stay in place.")}
          />
          {section.visible && (
            <CredentialRow
              label={t(credentialLabel(section.schema))}
              configured={section.removable}
              removing={section.removing}
              onRemovingChange={section.setRemoving}
              onDiscard={() => section.setCredential({})}
            >
              {definition && section.mode !== "forbidden" ? (
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
              ) : (
                <p className={styles.muted}>
                  {t(
                    "The backend definition is unavailable. Credential replacement requires its installed schema.",
                  )}
                </p>
              )}
            </CredentialRow>
          )}
          <ProviderFacts
            configuration={original.value.configuration}
            schema={configSchema}
          />
        </ProviderGroup>
      </ProviderEditorFields>
      {notices}
      <FormActions
        onCancel={onCancel}
        label={t("Save changes")}
        pending={save.isPending}
        leading={extra}
      />
    </ProviderEditor>
  );
}
