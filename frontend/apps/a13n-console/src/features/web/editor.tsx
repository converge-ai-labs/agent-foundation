import { useSuggestedName } from "../../shared/suggested-name";
import { FormSection, formSectionStyles } from "../../shared/form-section";
import { ResourceReference } from "../../shared/resource-reference";
import { Identifier } from "../../shared/copy";
import { ProviderTypeField } from "../../shared/provider-type-field";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { ApiError } from "../../service-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ReadOnlyField, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { ResourceIdentity } from "../../shared/collection";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { webProviderApi, type WebProviderScope } from "./api";

export function WebProviderEditor({
  scope,
  providerId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
  extra,
  readOnly = false,
}: ResourceEditorControl & {
  readOnly?: boolean;
  extra?: React.ReactNode;
  scope: WebProviderScope;
  providerId?: string;
  onSaved?: (provider: Schema["WebProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });

  const api = webProviderApi(client, scope);
  const definitions = useQuery({
    queryKey: ["web-provider-types"],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/web-provider-types", { signal }).then(data),
  });
  const resource = useQuery({
    queryKey: ["web-provider", scope.kind, scope.id, providerId],
    enabled: open && !!providerId,
    queryFn: ({ signal }) => api.provider(providerId!, signal),
  });
  return (
    <ModalFrame
      {...modalProps}
      size="lg"
      title={t(
        readOnly ? "Provider" : providerId ? "Edit provider" : "Add provider",
      )}
      description={
        providerId ? undefined : t("Connect a Web Provider for your agents.")
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
      {open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading variant="form" rows={4} />
        ) : definitions.error || resource.error ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
        ) : (
          definitions.data &&
          (readOnly ? (
            <div className={styles.stack}>
              {resource.data && (
                <ResourceIdentity
                  name={resource.data.value.name}
                  resourceId={resource.data.value.id}
                />
              )}
              {resource.data && (
                <div className={styles.twoColumns}>
                  <ReadOnlyField label={t("Provider type")}>
                    {definitions.data.items.find(
                      (item) => item.type === resource.data?.value.type,
                    )?.display_name ?? resource.data.value.type}
                  </ReadOnlyField>
                  <ReadOnlyField label={t("Status")}>
                    {t(resource.data.value.enabled ? "Enabled" : "Disabled")}
                  </ReadOnlyField>
                  <ReadOnlyField label={t("Credentials")}>
                    {t(
                      resource.data.value.credential_configured
                        ? "Configured"
                        : "Not configured",
                    )}
                  </ReadOnlyField>
                </div>
              )}
              {extra}
            </div>
          ) : (
            <WebProviderForm
              scope={scope}
              onCancel={() => setOpen(false)}
              extra={extra}
              resource={providerId ? resource.data : undefined}
              definitions={definitions.data.items}
              onSaved={(provider) => {
                onSaved?.(provider);
                setOpen(false);
              }}
            />
          ))
        ))}
    </ModalFrame>
  );
}

export function WebProviderForm({
  scope,
  resource,
  definitions,
  onSaved,
  onCancel,
  extra,
}: {
  scope: WebProviderScope;
  onCancel?: () => void;
  extra?: React.ReactNode;
  resource?: { value: Schema["WebProvider"]; etag?: string };
  definitions: Schema["WebProviderDefinition"][];
  onSaved: (provider: Schema["WebProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [original, setOriginal] = useState(resource),
    [type, setType] = useState(resource?.value.type ?? "brave"),
    { name, setName, suggestName } = useSuggestedName(resource?.value.name),
    [credential, setCredential] = useState(""),
    [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [reconciling, setReconciling] = useState(false),
    [existing, setExisting] = useState<Schema["WebProvider"][]>(),
    [reconcileError, setReconcileError] = useState<unknown>();
  const [reloadError, setReloadError] = useState<unknown>();
  const api = webProviderApi(client, scope),
    definition = definitions.find((item) => item.type === type);
  async function reconcile() {
    setReconciling(true);
    try {
      const items = await allPages((cursor) =>
        api.providers(new AbortController().signal, cursor),
      );
      setExisting(
        items.filter((item) =>
          scope.kind === "organization"
            ? item.workspace_id === null
            : item.workspace_id === scope.id,
        ),
      );
      setReconcileError(undefined);
    } catch (error) {
      setReconcileError(error);
    } finally {
      setReconciling(false);
    }
  }
  const save = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      if (!name.trim() || !definition)
        throw new Error(t("Choose a provider type and name."));
      if (
        (!original || credential) &&
        (!credential.trim() ||
          new TextEncoder().encode(credential).length > 4096)
      )
        throw new Error(t("Enter a nonblank API key of at most 4096 bytes."));
      if (!original) {
        if (type !== "brave" && type !== "exa")
          throw new Error(t("Choose a provider type."));
        return api.createProvider({
          type,
          name,
          credential: { api_key: credential },
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
        void reconcile();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
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
        <FormField
          label={t("Name")}
          labelAction={original && <ResourceReference id={original.value.id} />}
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
          readOnly={!!original}
          onValueChange={(value) => {
            setType(value);
            suggestName(
              definitions.find((item) => item.type === value)?.display_name ??
                value,
            );
            setCredential("");
          }}
          labelAction={
            definition && <ProviderKeyLink href={definition.setup_url} />
          }
        />
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
        {extra}
      </FormSection>
      {original && (
        <FormSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        </FormSection>
      )}
      <ErrorNotice error={reloadError ?? save.error ?? reconcileError} />
      {conflict && (
        <Button
          type="button"
          variant="outline"
          onClick={async () => {
            if (original) {
              try {
                const latest = await api.provider(
                  original.value.id,
                  new AbortController().signal,
                );
                setOriginal(latest);
                save.reset();
                setReloadError(undefined);
              } catch (error) {
                setReloadError(error);
              }
            }
          }}
        >
          {t("Load current version and keep my draft")}
        </Button>
      )}
      {reconciling && (
        <p role="status">
          {t("Checking existing providers before another create attempt…")}
        </p>
      )}
      {!!reconcileError && (
        <Button
          type="button"
          variant="outline"
          onClick={() => void reconcile()}
        >
          {t("Review existing providers")}
        </Button>
      )}
      {existing && (
        <div>
          <p>
            {t(
              "Review these saved providers before creating another. Stored API keys cannot be compared.",
            )}
          </p>
          {existing.map((item) => (
            <div key={item.id}>
              <strong>{item.name}</strong> · {item.type} ·{" "}
              <Identifier value={item.id} />
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setCredential("");
                  onSaved(item);
                }}
              >
                {t("Use this provider")}
              </Button>
            </div>
          ))}
        </div>
      )}
      <FormActions
        onCancel={onCancel}
        label={t(original ? "Save changes" : "Add provider")}
        pending={save.isPending || reconciling || !!reconcileError}
      />
    </form>
  );
}

export function WebProviderTest({
  scope,
  providerId,
  disabled = false,
}: {
  scope: WebProviderScope;
  providerId: string;
  disabled?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation();
  const test = useMutation({
    retry: false,
    mutationFn: () => webProviderApi(client, scope).testProvider(providerId),
  });
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || test.isPending}
          onClick={() => test.mutate()}
          title={t("Sends one test search and may consume provider quota.")}
        >
          {t(test.isPending ? "Testing…" : "Check connection")}
        </Button>
        <span className="text-xs text-muted-foreground">
          {t("May consume quota or incur cost.")}
        </span>
      </div>
      {test.data && (
        <p role="status">
          {test.data.success
            ? t("Test succeeded. This does not save the agent.")
            : t("Test failed: {{code}}", { code: test.data.code })}
        </p>
      )}
      <ErrorNotice error={test.error} />
    </div>
  );
}
