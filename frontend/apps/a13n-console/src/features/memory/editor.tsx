import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ReadOnlyField } from "a13n-ui";
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
import { ListRow, ListRows, ResourceIdentity } from "../../shared/collection";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import {
  CatalogStep,
  ConflictNotice,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  CredentialEditor,
  FormActions,
  FormSection,
  ProviderEnabled,
  SchemaFields,
  formSectionStyles,
  jsonObject,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import {
  AddProviderDialog,
  CredentialsPill,
  EditProviderDialog,
  ProviderConnectFields,
  credentialDescription,
  credentialHint,
  providerKeyUrls,
  providerStyles,
} from "../providers";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";

type Definition = Schema["MemoryProviderDefinition"];

export function useMemoryProviderDefinitions(
  scope: MemoryProviderScope,
  enabled = true,
) {
  const client = useClient();
  return useQuery({
    queryKey: ["memory-provider-types", scope.kind, scope.id],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/memory-provider-types", {
          signal,
          headers:
            scope.kind === "workspace" ? workspaceHeaders(scope.id) : undefined,
        })
        .then(data),
  });
}

/** Whether a backend asks for a secret at all. */
export function memoryCredentialState(
  provider: { credential_configured?: boolean },
  definition?: Definition,
) {
  const schema = (definition?.credential_schema ?? {}) as {
    properties?: Record<string, unknown>;
  };
  if (definition && !Object.keys(schema.properties ?? {}).length)
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
      readOnly={readOnly}
      description={definition?.display_name}
      loading={definitions.isPending || resource.isPending}
      error={resource.error}
    >
      {resource.data &&
        (readOnly ? (
          <div className={styles.stack}>
            <ResourceIdentity
              icon={<ProviderIcon type={resource.data.value.type} />}
              name={resource.data.value.name}
              description={definition?.display_name ?? resource.data.value.type}
            />
            <ConfigurationSummary
              value={resource.data.value.configuration}
              schema={definition?.configuration_schema ?? {}}
            />
            <div className={styles.twoColumns}>
              <ReadOnlyField label={t("Status")}>
                {t(resource.data.value.enabled ? "Enabled" : "Disabled")}
              </ReadOnlyField>
              <ReadOnlyField label={t("Credentials")}>
                <CredentialsPill
                  state={memoryCredentialState(resource.data.value, definition)}
                />
              </ReadOnlyField>
            </div>
            {extra}
          </div>
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
  const [credential, setCredential] = useState<Record<string, unknown>>({});
  const [removeCredential, setRemoveCredential] = useState(false);
  const [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [error, setError] = useState<unknown>();
  const [uncertainCreate, setUncertainCreate] = useState(false);
  const [existing, setExisting] = useState<Schema["MemoryProvider"][]>();
  const api = memoryProviderApi(client, scope);
  const definition = definitions.find((item) => item.type === type) ?? chosen;
  const configSchema = definition?.configuration_schema ?? {};
  const credentialSchema = definition?.credential_schema ?? {};
  const replacingCredential = Object.keys(credential).length > 0;
  const save = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      if (!name.trim()) throw new Error(t("Enter a provider name."));
      const credentials = withSchemaValues(credentialSchema, credential);
      if (!original || replacingCredential)
        validateSettings(credentialSchema, credentials);
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
            credential: jsonObject(JSON.stringify(credentials)),
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
        ...(removeCredential
          ? { credential: null }
          : replacingCredential
            ? { credential: jsonObject(JSON.stringify(credentials)) }
            : {}),
      });
    },
    onSuccess: (provider) => {
      setCredential({});
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
    <form className={formSectionStyles.form} onSubmit={submit}>
      <fieldset disabled={save.isPending} className="fieldset-reset">
        <FormSection>
          <FormField label={t("Name")}>
            <Input
              required
              maxLength={128}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        </FormSection>
        <FormSection title={t("Connection")}>
          <ConfigurationSummary
            value={original.value.configuration}
            schema={configSchema}
          />
          <p className={styles.muted}>
            {t(
              "Storage configuration cannot be changed. Create a new provider for a different target; existing memories are not migrated.",
            )}
          </p>
          <CredentialEditor
            configured={original.value.credential_configured}
            removing={removeCredential}
            onRemovingChange={(value) => {
              setRemoveCredential(value);
              setCredential({});
            }}
          >
            {definition ? (
              <SchemaFields
                secret
                schema={{ ...credentialSchema, required: [] }}
                value={credential}
                onChange={setCredential}
              />
            ) : (
              <p className={styles.muted}>
                {t(
                  "The backend definition is unavailable. Credential replacement requires its installed schema.",
                )}
              </p>
            )}
          </CredentialEditor>
        </FormSection>
        <FormSection>
          <p className={styles.muted}>
            {t(
              "Disabling a provider prevents memory access but does not delete stored records.",
            )}
          </p>
        </FormSection>
      </fieldset>
      {extra}
      {notices}
      <FormActions
        onCancel={onCancel}
        label={t("Save changes")}
        pending={save.isPending}
      />
    </form>
  );
}
