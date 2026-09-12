import {
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  Switch,
} from "a13n-ui";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

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
  "github@github_app_http_v1": "GitHub",
  "lark@lark_http_v1": "Lark",
  "slack@slack_http_v1": "Slack",
};

export function AccountForm({
  initial,
  onSuccess,
  onCancel,
  reload,
}: {
  initial?: Schema["Account"];
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
        : "",
    ),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.provider_config ?? {},
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
  const definition = definitions.data?.items.find(
    (item) => `${item.provider_key}@${item.config_version}` === provider,
  );
  const save = useMutation({
    mutationFn: async () => {
      if (!definition) throw new Error(t("Select an account provider."));
      validateSettings(definition.configuration_schema, configuration);
      if (Object.keys(policy).length)
        validateSettings(definition.reception_policy_schema, policy);
      const common = {
        name,
        provider_config: jsonObject(JSON.stringify(configuration)),
        receive_enabled: receive,
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
      validateSettings(definition.credential_schema, credentials);
      const body = {
        ...common,
        provider_key: definition.provider_key,
        provider_config_version: definition.config_version,
        credentials: stringValues(credentials),
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace}/application-accounts", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["application-accounts"] });
      onSuccess(result);
    },
  });
  if (definitions.isPending) return <Loading />;
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
        readOnly={!!basis}
        required
        onValueChange={(value) => {
          setProvider(value);
          setConfiguration({});
          setCredentials({});
          setPolicy({});
        }}
        label={t("Provider")}
        options={
          definitions.data?.items.map((item) => ({
            value: `${item.provider_key}@${item.config_version}`,
            label:
              accountProviderLabels[
                `${item.provider_key}@${item.config_version}`
              ] ?? `${item.provider_key} · ${item.config_version}`,
          })) ?? []
        }
      />
      {definition && (
        <>
          <SchemaFields
            key={provider}
            schema={definition.configuration_schema}
            value={configuration}
            onChange={setConfiguration}
          />
          {!basis && (
            <>
              <DisclosureSection title={t("Credentials")} defaultOpen>
                <SchemaFields
                  key={`${provider}-credentials`}
                  schema={definition.credential_schema}
                  value={credentials}
                  onChange={setCredentials}
                  secret
                />
              </DisclosureSection>
            </>
          )}
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
                  .map((item) => ({ value: item.id, label: item.name })) ?? []),
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
        </>
      )}
      <ErrorNotice
        error={save.error}
        retry={reload ? () => void reload() : undefined}
      />
      <FormActions
        onCancel={onCancel}
        pending={save.isPending}
        label={t(basis ? "Save changes" : "Create account")}
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
