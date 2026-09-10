import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import { ProviderIcon } from "../../shared/provider-icon";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { ApiError } from "@converge.ai/a13n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  SearchPicker,
  FormField,
  Input,
  SettingsSection,
  SettingsRow,
  ModalFrame,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { searchApi, type SearchScope } from "./api";

export function SearchProviderEditor({
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
  scope: SearchScope;
  providerId?: string;
  onSaved?: (provider: Schema["SearchProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });

  const api = searchApi(client, scope);
  const definitions = useQuery({
    queryKey: ["search-provider-types"],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/search-provider-types", { signal }).then(data),
  });
  const resource = useQuery({
    queryKey: ["search-provider", scope.kind, scope.id, providerId],
    enabled: open && !!providerId,
    queryFn: ({ signal }) => api.provider(providerId!, signal),
  });
  return (
    <ModalFrame
      {...modalProps}
      title={t(
        readOnly ? "Provider" : providerId ? "Edit provider" : "Add provider",
      )}
      description={t(
        "Credentials are stored securely and never returned by the service.",
      )}
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
          <Loading />
        ) : definitions.error || resource.error ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
        ) : (
          definitions.data &&
          (readOnly ? (
            <div className={styles.stack}>
              <p>{resource.data?.value.name}</p>
              {extra}
            </div>
          ) : (
            <SearchProviderForm
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

export function SearchProviderForm({
  scope,
  resource,
  definitions,
  onSaved,
  onCancel,
  extra,
}: {
  scope: SearchScope;
  onCancel?: () => void;
  extra?: React.ReactNode;
  resource?: { value: Schema["SearchProvider"]; etag?: string };
  definitions: Schema["SearchProviderDefinition"][];
  onSaved: (provider: Schema["SearchProvider"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [original, setOriginal] = useState(resource),
    [type, setType] = useState(resource?.value.type ?? "brave"),
    [name, setName] = useState(resource?.value.name ?? ""),
    [credential, setCredential] = useState(""),
    [enabled, setEnabled] = useState(resource?.value.enabled ?? true);
  const [reconciling, setReconciling] = useState(false),
    [existing, setExisting] = useState<Schema["SearchProvider"][]>(),
    [reconcileError, setReconcileError] = useState<unknown>();
  const [reloadError, setReloadError] = useState<unknown>();
  const api = searchApi(client, scope),
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
          credential,
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
        ...(credential ? { credential } : {}),
      });
    },
    onSuccess: (provider) => {
      setCredential("");
      void cache.invalidateQueries({ queryKey: ["search-providers"] });
      void cache.invalidateQueries({ queryKey: ["search-provider"] });
      onSaved(provider);
    },
    onError: (error) => {
      if (
        !original &&
        (!(error instanceof ApiError) ||
          error.status >= 500 ||
          error.code === "search_provider_name_conflict")
      )
        void reconcile();
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        save.mutate();
      }}
    >
      <FormField label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <FormField
        label={t("Provider type")}
        labelAction={
          definition && <ProviderKeyLink href={definition.setup_url} />
        }
      >
        <SearchPicker
          label={t("Provider type")}
          placeholder={t("Search providers…")}
          emptyMessage={t("No matching providers")}
          value={type}
          disabled={!!original}
          onValueChange={(value) => {
            setType(value);
            setCredential("");
          }}
          groups={[
            {
              label: "",
              options: definitions.map((item) => ({
                value: item.type,
                label: item.display_name,
                keywords: [item.type],
                icon: <ProviderIcon key={item.type} type={item.type} />,
              })),
            },
          ]}
        />
      </FormField>
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
          autoComplete="off"
          required={!original}
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
        />
      </FormField>
      {original && (
        <SettingsSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        </SettingsSection>
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
              <code>{item.id}</code>
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
      {extra}
      <FormActions
        onCancel={onCancel}
        pending={save.isPending || reconciling || !!reconcileError}
      />
    </form>
  );
}

export function SearchProviderTest({
  scope,
  providerId,
  disabled = false,
}: {
  scope: SearchScope;
  providerId: string;
  disabled?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation();
  const test = useMutation({
    retry: false,
    mutationFn: () => searchApi(client, scope).testProvider(providerId),
  });
  return (
    <div>
      <SettingsRow
        stackOnNarrow={false}
        label={t("Connection")}
        description={t(
          "Check the saved connection. May consume provider quota.",
        )}
      >
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
      </SettingsRow>
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
