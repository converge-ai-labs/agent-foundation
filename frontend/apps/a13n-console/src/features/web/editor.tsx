import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { ApiError } from "../../service-client";
import { allPages, data, type Schema } from "../../shared/api";
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
  ProviderGroup,
  ProviderName,
  ProviderReadOnly,
  providerKeyLink,
  credentialDescription,
  credentialHint,
  credentialLabel,
  providerStyles,
} from "../providers";
import { webProviderApi, type WebProviderScope } from "./api";

type Definition = Schema["WebProviderMetadata"];

function useWebProviderDefinitions(enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: ["web-provider-types"],
    enabled,
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/web-provider-types", { signal }).then(data),
  });
}

/** Catalog-first creation: choose the service, then connect it. */
export function AddWebProvider({
  scope,
  onSaved,
}: {
  scope: WebProviderScope;
  onSaved?: (provider: Schema["WebProvider"]) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  const definitions = useWebProviderDefinitions();
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
      description={t("Choose a search or scrape service for your agents.")}
      hint={(definition) => credentialHint(definition.credential_schema)}
      connectDescription={(definition) =>
        t(credentialDescription(definition.credential_schema), {
          provider: definition.display_name,
        })
      }
    >
      {(definition, back) => (
        <CatalogStep backLabel={t("All providers")} onBack={back}>
          <WebProviderForm
            scope={scope}
            definition={definition}
            definitions={definitions.data?.items ?? []}
            onCancel={() => change(false)}
            onSaved={(provider) => {
              onSaved?.(provider);
              change(false);
            }}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

/** Editing a saved provider, or reading one owned by another scope. */
export function WebProviderEditor({
  scope,
  providerId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
  readOnly = false,
}: ResourceEditorControl & {
  readOnly?: boolean;
  scope: WebProviderScope;
  providerId: string;
  onSaved?: (provider: Schema["WebProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const api = webProviderApi(client, scope);
  const definitions = useWebProviderDefinitions(state.open);
  const resource = useQuery({
    queryKey: ["web-provider", scope.kind, scope.id, providerId],
    enabled: state.open,
    queryFn: ({ signal }) => api.provider(providerId, signal),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === resource.data?.value.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={resource.data?.value.name}
      id={providerId}
      type={resource.data?.value.type}
      definition={definition?.display_name}
      scope={resource.data?.value.workspace_id ? "workspace" : "organization"}
      readOnly={readOnly}
      loading={definitions.isPending || resource.isPending}
      error={definitions.error ?? resource.error}
    >
      {definitions.data &&
        resource.data &&
        (readOnly ? (
          <ProviderReadOnly
            enabled={resource.data.value.enabled}
            credentials={
              credentialMode(definition, resource.data.value.configuration) !==
              "required"
                ? "not_required"
                : resource.data.value.credential_configured
                  ? "configured"
                  : "not_configured"
            }
            onClose={() => state.setOpen(false)}
          />
        ) : (
          <WebProviderForm
            scope={scope}
            resource={resource.data}
            definitions={definitions.data.items}
            onCancel={() => state.setOpen(false)}
            onSaved={(provider) => {
              onSaved?.(provider);
              state.setOpen(false);
            }}
          />
        ))}
    </EditProviderDialog>
  );
}

export function WebProviderForm({
  scope,
  resource,
  definition: chosen,
  definitions,
  onSaved,
  onCancel,
}: {
  scope: WebProviderScope;
  onCancel?: () => void;
  resource?: { value: Schema["WebProvider"]; etag?: string };
  /** Fixed by the catalog when creating. */
  definition?: Definition;
  definitions: Definition[];
  onSaved: (provider: Schema["WebProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [original, setOriginal] = useState(resource),
    type = resource?.value.type ?? chosen?.type ?? definitions[0]?.type ?? "",
    [name, setName] = useState(
      resource?.value.name ??
        chosen?.display_name ??
        definitions[0]?.display_name ??
        "",
    ),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      resource?.value.configuration ?? {},
    ),
    [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [existing, setExisting] = useState<Schema["WebProvider"][]>();
  const [reloadError, setReloadError] = useState<unknown>();
  const api = webProviderApi(client, scope),
    definition = definitions.find((item) => item.type === type) ?? chosen;
  const section = useCredentialSection(
    definition,
    configuration,
    original?.value,
  );
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
  const save = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      if (!name.trim() || !definition)
        throw new Error(t("Choose a provider type and name."));
      const config = withSchemaValues(
        definition.configuration_schema,
        configuration,
      );
      const credential = section.payload();
      if (!original) {
        return api.createProvider({
          type,
          name,
          ...(credential === undefined || credential === null
            ? {}
            : { credential }),
          configuration: config,
          enabled,
        });
      }
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, {
        name,
        enabled,
        configuration: config,
        ...(credential === undefined ? {} : { credential }),
      });
    },
    onSuccess: (provider) => {
      section.setCredential({});
      void cache.invalidateQueries({ queryKey: ["web-providers"] });
      void cache.invalidateQueries({ queryKey: ["web-provider"] });
      onSaved(provider);
    },
    onError: (error) => {
      if (
        !original &&
        (!(error instanceof ApiError) ||
          error.status >= 500 ||
          error.code === "web_provider_name_conflict")
      )
        reconcile.mutate();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  const unconfirmed = !original && (reconcile.isPending || !!existing);
  const notices = (
    <>
      <ErrorNotice error={reloadError ?? (conflict ? undefined : save.error)} />
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
                setReloadError(undefined);
              } catch (error) {
                setReloadError(error);
              }
            },
          }}
        />
      )}
      {unconfirmed && (
        <ConflictNotice
          title={t("Creation could not be confirmed")}
          description={t(
            "Review these saved providers before creating another. Stored API keys cannot be compared.",
          )}
          recover={{
            label: t("Review existing providers"),
            onClick: () => reconcile.mutate(),
            pending: reconcile.isPending,
          }}
          proceed={{
            label: t("I checked; allow another create attempt"),
            onClick: () => {
              setExisting(undefined);
              reconcile.reset();
              save.reset();
            },
          }}
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
                      onClick={() => {
                        section.setCredential({});
                        onSaved(item);
                      }}
                    >
                      {t("Use this provider")}
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
    </>
  );
  function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    save.mutate();
  }
  if (!original)
    return (
      <form className={providerStyles.connectForm} onSubmit={submit}>
        <ProviderConnectFields
          credentialSchema={
            section.mode === "forbidden" ? undefined : section.schema
          }
          credential={section.credential}
          onCredentialChange={section.setCredential}
          configurationSchema={definition?.configuration_schema}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyLink(definition)}
        />
        {notices}
        <FormActions
          onCancel={onCancel}
          label={t("Add provider")}
          pending={save.isPending}
          disabled={unconfirmed}
        />
      </form>
    );
  return (
    <ProviderEditor onSubmit={submit}>
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
      </ProviderGroup>
      {notices}
      <FormActions
        onCancel={onCancel}
        label={t("Save changes")}
        pending={save.isPending}
      />
    </ProviderEditor>
  );
}
