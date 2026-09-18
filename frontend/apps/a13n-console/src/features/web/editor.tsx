import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { ApiError } from "../../service-client";
import { allPages, data, type Schema } from "../../shared/api";
import { ListRow, ListRows, ResourceIdentity } from "../../shared/collection";
import {
  CatalogStep,
  ConflictNotice,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  FormSection,
  ProviderEnabled,
  ProviderKeyLink,
  ProviderTypeField,
  formSectionStyles,
} from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import {
  AddProviderDialog,
  CredentialsPill,
  EditProviderDialog,
} from "../providers";
import { webProviderApi, type WebProviderScope } from "./api";

type Definition = Schema["WebProviderDefinition"];

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
      hint={(definition) =>
        definition.credential_required ? "API key" : "No credentials"
      }
      connectDescription={(definition) =>
        definition.credential_required
          ? t("Paste the API key from your provider account.")
          : t("This service needs no credentials.")
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
      readOnly={readOnly}
      loading={definitions.isPending || resource.isPending}
      error={definitions.error ?? resource.error}
    >
      {definitions.data &&
        resource.data &&
        (readOnly ? (
          <div className={styles.stack}>
            <ResourceIdentity
              icon={<ProviderIcon type={resource.data.value.type} />}
              name={resource.data.value.name}
              description={definition?.display_name ?? resource.data.value.type}
            />
            <div className={styles.twoColumns}>
              <ReadOnlyField label={t("Status")}>
                {t(resource.data.value.enabled ? "Enabled" : "Disabled")}
              </ReadOnlyField>
              <ReadOnlyField label={t("Credentials")}>
                <CredentialsPill
                  state={
                    definition?.credential_required === false
                      ? "not_required"
                      : resource.data.value.credential_configured
                        ? "configured"
                        : "not_configured"
                  }
                />
              </ReadOnlyField>
            </div>
          </div>
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
    [credential, setCredential] = useState(""),
    [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [existing, setExisting] = useState<Schema["WebProvider"][]>();
  const [reloadError, setReloadError] = useState<unknown>();
  const api = webProviderApi(client, scope),
    definition = definitions.find((item) => item.type === type);
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
      if (
        definition.credential_required &&
        (!original || credential) &&
        (!credential.trim() ||
          new TextEncoder().encode(credential).length > 4096)
      )
        throw new Error(t("Enter a nonblank API key of at most 4096 bytes."));
      if (!original) {
        return api.createProvider({
          type,
          name,
          ...(definition.credential_required
            ? { credential: { api_key: credential } }
            : {}),
          configuration: {},
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
        ...(credential ? { credential: { api_key: credential } } : {}),
      });
    },
    onSuccess: (provider) => {
      setCredential("");
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
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        save.mutate();
      }}
    >
      <FormSection>
        <FormField label={t("Name")}>
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        {original && (
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        )}
      </FormSection>
      <FormSection title={t("Connection")}>
        <ProviderTypeField
          definitions={definitions}
          value={type}
          readOnly
          onValueChange={() => {}}
          labelAction={
            definition?.credential_required && (
              <ProviderKeyLink href={definition.setup_url} />
            )
          }
        />
        {definition?.credential_required && (
          <FormField
            label={t("API Key")}
            description={t(
              original
                ? "Leave empty to keep the current credential."
                : "The key is stored securely and cannot be read back.",
            )}
          >
            <Input
              type="password"
              placeholder={
                original?.value.credential_configured
                  ? t("Saved credential · enter to replace")
                  : undefined
              }
              autoComplete="new-password"
              name="search-api-key"
              required={!original}
              value={credential}
              onChange={(event) => setCredential(event.target.value)}
            />
          </FormField>
        )}
      </FormSection>
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
                        setCredential("");
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
      <FormActions
        onCancel={onCancel}
        label={t(original ? "Save changes" : "Add provider")}
        pending={save.isPending}
        disabled={unconfirmed}
      />
    </form>
  );
}
