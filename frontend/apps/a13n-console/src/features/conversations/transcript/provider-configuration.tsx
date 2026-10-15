import { Button, DisclosureSection } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import {
  JsonView,
  ProviderKeyLink,
  SchemaFields,
  validateSettings,
} from "../../../shared/forms";
import { useCredentialSection } from "../../../shared/use-credential-section";
import { providerApi } from "../../providers/api";
import { providerKeyLink } from "../../providers/key-urls";
import { providersPath } from "../../providers/navigation";
import styles from "./cards.module.css";

type Proposal = {
  action: "create" | "update";
  kind: "model" | "environment" | "connector" | "web";
  type: string;
  name: string;
  config: Record<string, Schema["JsonValue"]>;
  provider_id?: string | null;
  etag?: string | null;
};
type Result =
  | { status: "saved"; provider_id: string }
  | { status: "cancelled" | "unknown" };

function proposal(input: Schema["JsonValue"]): Proposal | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const value = input as Record<string, unknown>;
  const allowed = [
    "action",
    "kind",
    "type",
    "name",
    "config",
    "provider_id",
    "etag",
  ];
  if (Object.keys(value).some((key) => !allowed.includes(key))) return null;
  if (
    typeof value.action !== "string" ||
    !["create", "update"].includes(value.action) ||
    typeof value.kind !== "string" ||
    !["model", "environment", "connector", "web"].includes(value.kind) ||
    typeof value.type !== "string" ||
    !value.type.trim() ||
    typeof value.name !== "string" ||
    !value.name.trim() ||
    value.name.length > 128 ||
    !value.config ||
    typeof value.config !== "object" ||
    Array.isArray(value.config)
  )
    return null;
  if (
    value.action === "update" &&
    (typeof value.provider_id !== "string" ||
      !value.provider_id ||
      typeof value.etag !== "string" ||
      !value.etag)
  )
    return null;
  if (
    value.action === "create" &&
    (value.provider_id != null || value.etag != null)
  )
    return null;
  return value as Proposal;
}

/** Credentials stay in this card; its parent receives only an allowlisted result. */
export function ProviderConfiguration({
  arguments: arguments_,
  onResolved,
}: {
  arguments: Schema["JsonValue"];
  onResolved: (value: Result) => void;
}) {
  const { t } = useTranslation();
  const request = proposal(arguments_);
  const [cancelled, setCancelled] = useState(false);
  if (!request)
    return (
      <>
        <p>
          {t(
            "This provider request is invalid. Cancel it and ask the agent to prepare a new request.",
          )}
        </p>
        <Button
          type="button"
          variant="outline"
          disabled={cancelled}
          onClick={() => {
            setCancelled(true);
            onResolved({ status: "cancelled" });
          }}
        >
          {t(cancelled ? "Cancelled" : "Cancel configuration")}
        </Button>
      </>
    );
  return (
    <ProviderConfigurationFields request={request} onResolved={onResolved} />
  );
}

function ProviderConfigurationFields({
  request,
  onResolved,
}: {
  request: Proposal;
  onResolved: (value: Result) => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const queries = useQueryClient();
  const api = providerApi(client, workspace.id, request.kind);
  const [result, setResult] = useState<Result | null>(null);
  const [saveAttempted, setSaveAttempted] = useState(false);
  const types = useQuery({
    queryKey: [`${request.kind}-provider-types`],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: request.kind } },
          signal,
        })
        .then(data),
  });
  const saved = useQuery({
    queryKey: [`${request.kind}-providers`, workspace.id, request.provider_id],
    enabled: request.action === "update",
    queryFn: ({ signal }) => api.provider(request.provider_id!, signal),
  });
  const definition = types.data?.items.find(
    (item) => item.type === request.type,
  );
  const credential = useCredentialSection(
    definition,
    request.config,
    saved.data?.value,
  );
  const ready =
    !!definition &&
    (request.action === "create" || saved.data?.value.type === request.type);
  const keyLink = providerKeyLink(definition);
  function resolve(value: Result) {
    credential.setCredential({});
    setResult(value);
    onResolved(value);
  }
  const save = useMutation({
    retry: false,
    mutationFn: async (): Promise<Result> => {
      if (!can("write") || !can("run") || !ready || result)
        throw new Error(t("Provider configuration is unavailable."));
      // Never retain credentials in mutation variables, cached results or error messages.
      let secret: ReturnType<typeof credential.payload>;
      try {
        secret = credential.payload();
        if (secret) validateSettings(credential.schema, secret);
        validateSettings(definition!.configuration_schema, request.config);
      } catch {
        throw new Error(
          t(
            "Check the required credential fields and proposed configuration before saving.",
          ),
        );
      }
      try {
        setSaveAttempted(true);
        const body = {
          name: request.name,
          config: request.config,
          ...(secret !== undefined
            ? { credential: secret as Schema["ProviderCreate"]["credential"] }
            : {}),
        };
        const provider =
          request.action === "create"
            ? await api.createProvider({ ...body, type: request.type })
            : await api.updateProvider(
                request.provider_id!,
                request.etag!,
                body,
              );
        return { status: "saved", provider_id: provider.id };
      } catch {
        throw new Error(
          t(
            "Could not confirm the save. Check the Providers page before retrying; another create may add a duplicate. For an update conflict, cancel and ask for a fresh request.",
          ),
        );
      }
    },
    onSuccess: (value) => {
      resolve(value);
      void queries.invalidateQueries({
        queryKey: [`${request.kind}-providers`, workspace.id],
      });
    },
  });
  const tabs = {
    model: "models",
    environment: "environments",
    connector: "connectors",
    web: "web",
  };
  const kindLabels = {
    model: "Model",
    environment: "Environment",
    connector: "Connector",
    web: "Web",
  };
  return (
    <div className="grid gap-4">
      <dl className={styles.cardFacts}>
        <dt>{t("Action")}</dt>
        <dd>
          {t(
            request.action === "create" ? "Create provider" : "Update provider",
          )}
        </dd>
        <dt>{t("Name")}</dt>
        <dd>{request.name}</dd>
        <dt>{t("Type")}</dt>
        <dd>
          {definition?.display_name ?? request.type} ·{" "}
          {t(kindLabels[request.kind])}
        </dd>
        {request.provider_id && (
          <>
            <dt>{t("Provider ID")}</dt>
            <dd>{request.provider_id}</dd>
          </>
        )}
      </dl>
      <DisclosureSection title={t("Configuration")}>
        <JsonView value={request.config} />
      </DisclosureSection>
      <p className="text-sm text-muted-foreground">
        {t(
          "Credentials go directly to the provider settings. The agent receives only the save status and provider ID.",
        )}
      </p>
      <a
        className="text-sm underline underline-offset-4"
        href={providersPath(tabs[request.kind], workspace)}
        target="_blank"
        rel="noreferrer"
      >
        {t("Open Providers")}
      </a>
      {result ? (
        <p role="status">
          {t(
            result.status === "saved"
              ? "Provider saved. Submit responses to continue; retrying that step will not save the provider again."
              : result.status === "unknown"
                ? "Save outcome unknown. Check Providers before requesting another write. Submit responses to inform the agent."
                : "Configuration cancelled. Submit responses to continue.",
          )}
        </p>
      ) : (
        <>
          {!ready && (
            <p role="status">
              {t(
                types.isPending ||
                  (request.action === "update" && saved.isPending)
                  ? "Loading provider settings…"
                  : "Provider configuration is unavailable.",
              )}
            </p>
          )}
          {!can("write") && (
            <p>{t("You need write access to configure providers.")}</p>
          )}
          <fieldset
            disabled={!ready || !can("write") || save.isPending}
            className="grid gap-4"
          >
            {credential.mode !== "forbidden" && (
              <SchemaFields
                secret
                schema={credential.schema}
                value={credential.credential}
                onChange={credential.setCredential}
                requireFields={credential.requireFields}
                labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
              />
            )}
            {saved.data?.value.credential_configured && (
              <p className="text-sm text-muted-foreground">
                {t(
                  credential.mode === "forbidden"
                    ? "Saving this configuration removes the saved credentials because authentication is disabled."
                    : "Leave credentials empty to keep the saved credentials.",
                )}
              </p>
            )}
            <Button type="button" onClick={() => save.mutate()}>
              {t(save.isPending ? "Saving…" : "Save provider")}
            </Button>
          </fieldset>
          <ErrorNotice error={save.error} />
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={() =>
              resolve({ status: saveAttempted ? "unknown" : "cancelled" })
            }
          >
            {t("Cancel configuration")}
          </Button>
        </>
      )}
    </div>
  );
}
