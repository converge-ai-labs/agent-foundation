import {
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  Switch,
} from "a13n-ui";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ApiError } from "../../service-client";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import {
  jsonObject,
  stringValues,
  validateSettings,
} from "../../shared/validation";
import { useAccountProviders, useReceptionOptions } from "./data";

const accountProviderLabels: Record<string, string> = {
  "github@github_app_http_v1": "GitHub App · Webhook",
  "github@github_notifications_v1": "GitHub account · Polling",
  "lark@lark_http_v1": "Lark",
  "slack@slack_http_v1": "Slack",
};

export function AccountForm({
  initial,
  onSuccess,
  onCancel,
  reload,
  bot = false,
  setupProvider,
}: {
  initial?: Schema["Account"];
  bot?: boolean;
  setupProvider?: "slack" | "lark" | "github" | "github_polling";
  onSuccess: (account: Schema["Account"]) => void;
  onCancel: () => void;
  reload?: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    definitions = useAccountProviders(),
    options = useReceptionOptions(true);
  const [basis] = useState(initial),
    [name, setName] = useState(initial?.name ?? ""),
    [provider, setProvider] = useState(
      initial
        ? `${initial.provider_key}@${initial.provider_config_version}`
        : setupProvider
          ? setupProvider === "github"
            ? "github@github_app_http_v1"
            : setupProvider === "github_polling"
              ? "github@github_notifications_v1"
              : `${setupProvider}@${setupProvider}_http_v1`
          : "",
    ),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.provider_config ??
        (setupProvider === "lark"
          ? { brand: "feishu", open_api_origin: "https://open.feishu.cn" }
          : setupProvider?.startsWith("github")
            ? {
                api_origin: "https://api.github.com",
                web_origin: "https://github.com",
              }
            : {}),
    ),
    [credentials, setCredentials] = useState<Record<string, unknown>>({}),
    [policy, setPolicy] = useState<Record<string, unknown>>(
      initial?.provider_policy ?? {},
    ),
    [receive, setReceive] = useState(initial?.receive_enabled ?? false),
    [agentId, setAgentId] = useState(initial?.default_agent_id ?? ""),
    [serviceAccountId, setServiceAccountId] = useState(
      initial?.execution_service_account_id ?? "",
    ),
    [batching, setBatching] = useState<Schema["InputBatchingPolicy"] | null>(
      initial?.input_batching ?? null,
    );
  const setupCommand = useRef<string | null>(null);
  const discoveredInstallation = useRef<{
    fingerprint: string;
    installation: Schema["InstallationInfo"];
  } | null>(null);
  const definition = definitions.data?.items.find(
    (item) => `${item.provider_key}@${item.config_version}` === provider,
  );
  const messaging =
    definition && ["slack", "lark"].includes(definition.provider_key);
  const websocket = configuration.event_transport === "websocket";
  const credentialSchema =
    definition &&
    transportCredentials(
      definition.credential_schema,
      definition.provider_key,
      websocket,
    );
  const configurationSchema =
    definition && setupProvider === "lark"
      ? {
          ...definition.configuration_schema,
          required: ["app_id"],
          properties: Object.fromEntries(
            Object.entries(
              (definition.configuration_schema.properties ?? {}) as Record<
                string,
                unknown
              >,
            ).filter(([field]) => field === "app_id"),
          ),
        }
      : definition && setupProvider === "github_polling"
        ? {
            ...definition.configuration_schema,
            required: [],
            properties: Object.fromEntries(
              Object.entries(
                (definition.configuration_schema.properties ?? {}) as Record<
                  string,
                  unknown
                >,
              ).filter(
                ([field]) =>
                  !["user_id", "api_origin", "web_origin"].includes(field),
              ),
            ),
          }
        : definition &&
          withoutField(definition.configuration_schema, "event_transport");
  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (!definition) throw new Error(t("Select an account provider."));
      validateSettings(
        configurationSchema!,
        setupProvider === "lark"
          ? { app_id: configuration.app_id }
          : Object.fromEntries(
              Object.entries(configuration).filter(
                ([field]) => field !== "event_transport",
              ),
            ),
      );
      let accountConfiguration = configuration;
      if (setupProvider === "lark" && !basis) {
        validateSettings(credentialSchema!, credentials);
        const appId = stringValues(configuration).app_id;
        const appSecret = stringValues(credentials).app_secret;
        const fingerprint = await setupFingerprint({
          app_id: appId,
          app_secret: appSecret,
        });
        if (
          setupCommand.current &&
          discoveredInstallation.current?.fingerprint !== fingerprint
        )
          throw new Error(
            t(
              "The previous save is unconfirmed. Re-enter the same credentials and retry the unchanged setup to recover its result.",
            ),
          );
        if (discoveredInstallation.current?.fingerprint !== fingerprint) {
          const installation = await client.http
            .POST("/api/v1/workspaces/{workspace}/bots/feishu/installation", {
              params: { path: { workspace: workspace.id } },
              body: { app_id: appId, app_secret: appSecret },
            })
            .then(data);
          discoveredInstallation.current = { fingerprint, installation };
        }
        const installation = discoveredInstallation.current.installation;
        accountConfiguration = {
          event_transport: websocket ? "websocket" : "http",
          brand: "feishu",
          open_api_origin: "https://open.feishu.cn",
          app_id: installation.app_id,
          tenant_key: installation.organization_id,
          bot_open_id: installation.bot_id,
        };
      }
      if (setupProvider === "github_polling" && !basis) {
        validateSettings(definition.credential_schema, credentials);
        const installation = await client.http
          .POST("/api/v1/workspaces/{workspace}/bots/github/user", {
            params: { path: { workspace: workspace.id } },
            body: {
              personal_access_token:
                stringValues(credentials).personal_access_token,
            },
          })
          .then(data);
        accountConfiguration = {
          ...configuration,
          user_id: Number(installation.bot_id),
        };
      }
      if (Object.keys(policy).length)
        validateSettings(definition.reception_policy_schema, policy);
      const common = {
        name,
        provider_config: jsonObject(JSON.stringify(accountConfiguration)),
        receive_enabled: setupProvider ? false : receive,
        reception_scope:
          basis?.reception_scope ??
          (bot || setupProvider
            ? ("configured_targets" as const)
            : ("all_accessible" as const)),
        default_agent_id: agentId || null,
        execution_service_account_id: serviceAccountId || null,
        input_batching: batching,
        provider_policy: Object.keys(policy).length
          ? jsonObject(JSON.stringify(policy))
          : null,
      };
      if (basis)
        return client.http
          .PATCH("/api/v1/application-accounts/{account_id}", {
            params: { path: { account_id: basis.id } },
            body: { ...common, expected_version: basis.version },
          })
          .then(data);
      validateSettings(credentialSchema!, credentials);
      const body = {
        ...common,
        provider_key: definition.provider_key,
        provider_config_version: definition.config_version,
        credentials: stringValues(credentials),
      };
      const digest = setupProvider ? await setupFingerprint(body) : null;
      if (digest && setupCommand.current && setupCommand.current !== digest)
        throw new Error(
          t(
            "The previous save is unconfirmed. Re-enter the same credentials and retry the unchanged setup to recover its result.",
          ),
        );
      if (digest) setupCommand.current = digest;
      return client.http
        .POST("/api/v1/workspaces/{workspace}/application-accounts", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(digest ?? body)),
          },
          body,
        })
        .then(data);
    },
    onError: (error) => {
      if (
        setupProvider &&
        error instanceof ApiError &&
        error.status < 500 &&
        error.status !== 409
      )
        setupCommand.current = null;
    },
    onSettled: () => {
      if (setupProvider) setCredentials({});
    },
    onSuccess: (result) => {
      setCredentials({});
      key.reset();
      void cache.invalidateQueries({ queryKey: ["application-accounts"] });
      void cache.invalidateQueries({ queryKey: ["bots"] });
      onSuccess(result);
    },
  });
  if (definitions.isPending) return <Loading variant="form" rows={5} />;
  return (
    <form
      autoComplete="off"
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice
        error={
          definitions.error ?? options.agents.error ?? options.accounts.error
        }
      />
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <ChoiceField
        placeholder={t("Select account provider")}
        value={provider}
        className="min-w-0"
        readOnly={!!basis || !!setupProvider}
        required
        onValueChange={(value) => {
          setProvider(value);
          setConfiguration({});
          setCredentials({});
          setPolicy({});
        }}
        label={t("Provider")}
        options={
          definitions.data?.items
            .filter(
              (item) =>
                !bot || ["slack", "lark", "github"].includes(item.provider_key),
            )
            .map((item) => ({
              value: `${item.provider_key}@${item.config_version}`,
              label:
                (setupProvider === "lark" ? t("Feishu") : undefined) ??
                accountProviderLabels[
                  `${item.provider_key}@${item.config_version}`
                ] ??
                `${item.provider_key} · ${item.config_version}`,
            })) ?? []
        }
      />
      {definition && (
        <>
          {messaging && (
            <>
              <ChoiceField
                label={t("Event connection")}
                value={websocket ? "websocket" : "http"}
                options={[
                  { value: "http", label: t("HTTP callback") },
                  {
                    value: "websocket",
                    label: t(
                      definition.provider_key === "slack"
                        ? "Socket Mode"
                        : "Long connection (WebSocket)",
                    ),
                  },
                ]}
                onValueChange={(value) => {
                  setConfiguration({
                    ...configuration,
                    event_transport: value,
                  });
                  setCredentials({});
                }}
              />
              <p className={styles.muted}>
                {t(
                  websocket
                    ? "The service connects to the platform. No public callback URL is needed."
                    : "The platform sends events to your public HTTPS callback URL.",
                )}
              </p>
              {websocket && definition.provider_key === "slack" && (
                <p className={styles.muted}>
                  {t(
                    "Use an app-level token with connections:write and enable Socket Mode in Slack. Installations of the same app must use the same app-level token.",
                  )}
                </p>
              )}
              {basis && (
                <p className={styles.muted}>
                  {t(
                    "Before switching, update the account credentials for the new connection method. Keep both methods' credentials during the switch.",
                  )}
                </p>
              )}
            </>
          )}
          <SchemaFields
            key={provider}
            schema={configurationSchema!}
            value={configuration}
            onChange={setConfiguration}
          />
          {!basis && (
            <>
              <DisclosureSection title={t("Credentials")} defaultOpen>
                <SchemaFields
                  key={`${provider}-credentials`}
                  schema={credentialSchema!}
                  value={credentials}
                  onChange={setCredentials}
                  secret
                />
              </DisclosureSection>
            </>
          )}
          {!setupProvider && (
            <DisclosureSection title={t("Reception")} defaultOpen={receive}>
              <Label className="flex items-center gap-2">
                <Switch checked={receive} onCheckedChange={setReceive} />
                {t("Receive events")}
              </Label>
              <ChoiceField
                placeholder={t("Select agent")}
                value={agentId || "none"}
                className="min-w-0"
                required={receive}
                onValueChange={(value) =>
                  setAgentId(value === "none" ? "" : value)
                }
                label={t("Default agent")}
                options={[
                  { value: "none", label: t("No default agent") },
                  ...(options.agents.data?.map((item) => ({
                    value: item.id,
                    label: item.name,
                  })) ?? []),
                ]}
              />
              <ChoiceField
                placeholder={t("Select service account")}
                value={serviceAccountId || "none"}
                className="min-w-0"
                required={receive}
                onValueChange={(value) =>
                  setServiceAccountId(value === "none" ? "" : value)
                }
                label={t("Execution service account")}
                options={[
                  { value: "none", label: t("No execution identity") },
                  ...(options.accounts.data
                    ?.filter((item) => item.status === "active")
                    .map((item) => ({ value: item.id, label: item.name })) ??
                    []),
                ]}
              />
              <BatchingFields value={batching} onChange={setBatching} />
              <DisclosureSection title={<>{t("Provider reception policy")}</>}>
                <SchemaFields
                  key={`${provider}-policy`}
                  schema={definition.reception_policy_schema}
                  value={policy}
                  onChange={setPolicy}
                />
              </DisclosureSection>
            </DisclosureSection>
          )}
        </>
      )}
      <ErrorNotice
        error={save.error}
        retry={reload ? () => void reload() : undefined}
      />
      <FormActions
        onCancel={onCancel}
        pending={save.isPending}
        label={t(
          basis
            ? "Save changes"
            : setupProvider
              ? "Save and verify"
              : "Create account",
        )}
      />
    </form>
  );
}
export function BatchingFields({
  value,
  onChange,
}: {
  value: Schema["InputBatchingPolicy"] | null;
  onChange: (value: Schema["InputBatchingPolicy"] | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.stack}>
      <Label className="flex items-center gap-2">
        <Switch
          checked={value !== null}
          onCheckedChange={(enabled) =>
            onChange(
              enabled ? { min_interval_ms: 1000, max_batch_events: 10 } : null,
            )
          }
        />
        {t("Custom input batching")}
      </Label>
      {value && (
        <>
          <FormField
            className="min-w-0 w-full"
            label={t("Minimum interval (milliseconds)")}
          >
            <Input
              required={true}
              type="number"
              min={1}
              step={1}
              value={value.min_interval_ms}
              onChange={(event) =>
                onChange({
                  ...value,
                  min_interval_ms: Number(event.target.value),
                })
              }
            />
          </FormField>
          <FormField
            className="min-w-0 w-full"
            label={t("Maximum events per batch")}
          >
            <Input
              required={true}
              type="number"
              min={1}
              step={1}
              value={value.max_batch_events}
              onChange={(event) =>
                onChange({
                  ...value,
                  max_batch_events: Number(event.target.value),
                })
              }
            />
          </FormField>
        </>
      )}
    </div>
  );
}

async function setupFingerprint(value: unknown): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      JSON.stringify(value, (_name, item: unknown) =>
        item !== null && typeof item === "object" && !Array.isArray(item)
          ? Object.fromEntries(
              Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
            )
          : item,
      ),
    ),
  );
  return Array.from(new Uint8Array(bytes))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function withoutField(
  schema: Record<string, unknown>,
  field: string,
): Record<string, unknown> {
  return {
    ...schema,
    properties: Object.fromEntries(
      Object.entries(
        (schema.properties ?? {}) as Record<string, unknown>,
      ).filter(([key]) => key !== field),
    ),
    required: ((schema.required ?? []) as string[]).filter(
      (key) => key !== field,
    ),
  };
}

function transportCredentials(
  schema: Record<string, unknown>,
  provider: string,
  websocket: boolean,
): Record<string, unknown> {
  if (!["slack", "lark"].includes(provider)) return schema;
  let result = schema;
  for (const field of provider === "slack"
    ? [websocket ? "signing_secret" : "app_token"]
    : websocket
      ? ["verification_token", "encrypt_key"]
      : [])
    result = withoutField(result, field);
  const required =
    provider === "slack"
      ? ["bot_token", websocket ? "app_token" : "signing_secret"]
      : websocket
        ? ["app_secret"]
        : ["app_secret", "verification_token"];
  return { ...result, required };
}
