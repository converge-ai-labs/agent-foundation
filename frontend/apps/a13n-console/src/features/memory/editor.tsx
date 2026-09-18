import { ApiError } from "../../service-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame, ReadOnlyField } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import { CredentialEditor } from "../../shared/forms";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { FormSection, formSectionStyles } from "../../shared/forms";
import { ProviderEnabled } from "../../shared/forms";
import { ProviderTypeField } from "../../shared/forms";
import { ResourceEditorButton } from "../../shared/identity";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ResourceReference } from "../../shared/identity";
import { SchemaFields, withSchemaValues } from "../../shared/forms";
import { useSuggestedName } from "../../shared/forms";
import { jsonObject, validateSettings } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";

export function MemoryProviderEditor({
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
  providerId?: string;
  extra?: ReactNode;
  readOnly?: boolean;
  onSaved?: (provider: Schema["MemoryProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const [pending, setPending] = useState(false);
  const definitions = useQuery({
    queryKey: ["memory-provider-types", scope.kind, scope.id],
    enabled: state.open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/memory-provider-types", {
          signal,
          headers:
            scope.kind === "workspace" ? workspaceHeaders(scope.id) : undefined,
        })
        .then(data),
  });
  const resource = useQuery({
    queryKey: ["memory-provider", scope.kind, scope.id, providerId],
    enabled: state.open && !!providerId,
    queryFn: ({ signal }) =>
      memoryProviderApi(client, scope).provider(providerId!, signal),
  });
  return (
    <ModalFrame
      {...state.modalProps}
      onOpenChange={(open, details) => {
        if (pending) details.cancel();
        else state.modalProps.onOpenChange(open, details);
      }}
      size="lg"
      title={t(
        readOnly ? "Provider" : providerId ? "Edit provider" : "Add provider",
      )}
      description={
        !providerId
          ? t(
              "Connect a memory backend. Saving configuration does not test the connection.",
            )
          : undefined
      }
      closeLabel={t("Close")}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton
            editing={!!providerId}
            createLabel="Add provider"
            editLabel="Edit"
          />
        ) : undefined
      }
    >
      {state.open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading variant="form" rows={4} />
        ) : resource.error || (!providerId && definitions.error) ? (
          <ErrorNotice error={resource.error ?? definitions.error} />
        ) : (
          <MemoryProviderForm
            scope={scope}
            resource={providerId ? resource.data : undefined}
            definitions={definitions.data?.items ?? []}
            readOnly={readOnly}
            extra={extra}
            onPending={setPending}
            onCancel={() => state.setOpen(false)}
            onSaved={(provider) => {
              state.setOpen(false);
              onSaved?.(provider);
            }}
          />
        ))}
    </ModalFrame>
  );
}

export function MemoryProviderForm({
  scope,
  resource,
  definitions,
  readOnly = false,
  extra,
  onCancel,
  onSaved,
  onPending,
}: {
  scope: MemoryProviderScope;
  resource?: { value: Schema["MemoryProvider"]; etag?: string };
  definitions: Schema["MemoryProviderDefinition"][];
  readOnly?: boolean;
  extra?: ReactNode;
  onCancel: () => void;
  onSaved: (provider: Schema["MemoryProvider"]) => void;
  onPending?: (pending: boolean) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [original, setOriginal] = useState(resource);
  const [type, setType] = useState(
    resource?.value.type ?? definitions[0]?.type ?? "",
  );
  const { name, setName, suggestName } = useSuggestedName(
    resource?.value.name ?? definitions[0]?.display_name,
  );
  const [configuration, setConfiguration] = useState<Record<string, unknown>>(
    resource?.value.configuration ?? {},
  );
  const [credential, setCredential] = useState<Record<string, unknown>>({});
  const [removeCredential, setRemoveCredential] = useState(false);
  const [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [error, setError] = useState<unknown>();
  const [uncertainCreate, setUncertainCreate] = useState(false);
  const [existing, setExisting] = useState<Schema["MemoryProvider"][]>();
  const api = memoryProviderApi(client, scope);
  const definition = definitions.find((item) => item.type === type);
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
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!readOnly && !save.isPending && !uncertainCreate) {
          onPending?.(true);
          save.mutate();
        }
      }}
    >
      <fieldset disabled={save.isPending} className="fieldset-reset">
        <FormSection>
          <FormField
            readOnly={readOnly}
            label={t("Name")}
            labelAction={
              original && <ResourceReference id={original.value.id} />
            }
          >
            <Input
              required
              maxLength={128}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
        </FormSection>
        <FormSection title={t("Connection")}>
          <ProviderTypeField
            definitions={definitions}
            value={type}
            readOnly={!!original || readOnly}
            onValueChange={(value) => {
              setType(value);
              setConfiguration({});
              setCredential({});
              suggestName(
                definitions.find((item) => item.type === value)?.display_name ??
                  value,
              );
            }}
          />
          {original ? (
            <>
              <ConfigurationSummary
                value={original.value.configuration}
                schema={configSchema}
              />
              <p className="text-sm text-muted-foreground">
                {t(
                  "Storage configuration cannot be changed. Create a new provider for a different target; existing memories are not migrated.",
                )}
              </p>
            </>
          ) : (
            <SchemaFields
              key={type}
              schema={configSchema}
              value={configuration}
              onChange={setConfiguration}
            />
          )}
        </FormSection>
        <FormSection title={t("Credentials")}>
          {readOnly ? (
            <ReadOnlyField label={t("Credentials")}>
              {t(
                original?.value.credential_configured
                  ? "Configured"
                  : "Not configured",
              )}
            </ReadOnlyField>
          ) : original ? (
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
                <p className="text-sm text-muted-foreground">
                  {t(
                    "The backend definition is unavailable. Credential replacement requires its installed schema.",
                  )}
                </p>
              )}
            </CredentialEditor>
          ) : (
            <SchemaFields
              secret
              key={`${type}-credentials`}
              schema={credentialSchema}
              value={credential}
              onChange={setCredential}
            />
          )}
        </FormSection>
        {original && (
          <FormSection>
            {readOnly ? (
              <ReadOnlyField label={t("Status")}>
                {t(enabled ? "Enabled" : "Disabled")}
              </ReadOnlyField>
            ) : (
              <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
            )}
            <p className="text-sm text-muted-foreground">
              {t(
                "Disabling a provider prevents memory access but does not delete stored records.",
              )}
            </p>
          </FormSection>
        )}
      </fieldset>
      {extra}
      <ErrorNotice error={error ?? save.error} />
      {conflict && (
        <Button
          variant="outline"
          onClick={async () => {
            try {
              setOriginal(
                await api.provider(
                  original!.value.id,
                  new AbortController().signal,
                ),
              );
              save.reset();
              setError(undefined);
            } catch (error) {
              setError(error);
            }
          }}
        >
          {t("Load current version and keep my draft")}
        </Button>
      )}
      {uncertainCreate && (
        <div className={styles.stack}>
          <p role="alert">
            {t(
              "Creation could not be confirmed. Review saved providers before creating another; credentials cannot be compared.",
            )}
          </p>
          <Button
            variant="outline"
            loading={reconcile.isPending}
            onClick={() => reconcile.mutate()}
          >
            {t("Review existing providers")}
          </Button>
          <ErrorNotice error={reconcile.error} />
          {existing && (
            <>
              {existing.map((item) => (
                <div
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2"
                >
                  <span>
                    {item.name} · {item.type}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onSaved(item)}
                  >
                    {t("Return to providers")}
                  </Button>
                </div>
              ))}
              {!existing.length && (
                <p>{t("No saved providers found in this scope.")}</p>
              )}
              <Button
                variant="outline"
                onClick={() => {
                  setUncertainCreate(false);
                  save.reset();
                }}
              >
                {t("I checked; allow another create attempt")}
              </Button>
            </>
          )}
        </div>
      )}
      {!readOnly && (
        <FormActions
          onCancel={onCancel}
          label={t(original ? "Save changes" : "Add provider")}
          pending={save.isPending}
          disabled={uncertainCreate}
        />
      )}
    </form>
  );
}
