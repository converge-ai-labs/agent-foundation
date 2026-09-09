import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
  Switch,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions, TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import {
  inputOverride,
  jsonObject,
  validateSettings,
} from "../../shared/validation";
import { useAccountProviders, useReceptionOptions } from "./data";
import { BatchingFields } from "./form";

export function AccountTargets({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "account-targets",
      account.workspace_id,
      account.id,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/targets", {
          params: {
            path: { account_id: account.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          {t("Override routing for one exact conversation or repository.")}
        </p>
        {can("account_target.manage") && <TargetEditor account={account} />}
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Target"),
                render: (item) => (
                  <>
                    <strong>{item.external_target_id}</strong>
                    <small>
                      {t(
                        item.target_kind === "repository"
                          ? "Repository"
                          : "Conversation",
                      )}
                    </small>
                  </>
                ),
              },
              {
                label: t("Agent"),
                render: (item) => item.agent_id ?? t("Account default"),
              },
              {
                label: t("Reception"),
                render: (item) => (
                  <StateBadge
                    state={item.receive_enabled ? "enabled" : "disabled"}
                  />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) =>
                  can("account_target.manage") && (
                    <div className={styles.actions}>
                      <TargetEditor account={account} target={item} />
                      <Confirm
                        title={t("Delete target override")}
                        description={t(
                          "The account's default routing will apply to future events for this target.",
                        )}
                        trigger={t("Delete")}
                        danger
                        action={() =>
                          client.http.DELETE(
                            "/api/v1/application-accounts/{account_id}/targets/{target_id}",
                            {
                              params: {
                                path: {
                                  account_id: account.id,
                                  target_id: item.id,
                                },
                                query: { expected_version: item.version },
                              },
                            },
                          )
                        }
                      />
                    </div>
                  ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No target overrides")}
            description={t(
              "Incoming events use the account defaults unless an exact target overrides them.",
            )}
          />
        )
      )}
    </div>
  );
}
function TargetEditor({
  account,
  target,
}: {
  account: Schema["Account"];
  target?: Schema["AccountTarget"];
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button
          size="sm"
          variant={target ? "outline" : "default"}
          type="button"
        >
          {t(target ? "Edit" : "Add target")}
        </Button>
      }
      size={"md"}
      title={t(target ? "Edit target override" : "Add target override")}
      description={t("Match one provider object by its exact identifier.")}
      closeLabel={t("Close")}
      open={open}
    >
      {open && (
        <TargetForm
          account={account}
          initial={target}
          close={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}
function TargetForm({
  account,
  initial,
  close,
}: {
  account: Schema["Account"];
  initial?: Schema["AccountTarget"];
  close: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    options = useReceptionOptions(false),
    definitions = useAccountProviders();
  const [basis, setBasis] = useState(initial),
    [kind, setKind] = useState<"conversation" | "repository">(
      initial?.target_kind ??
        (account.provider_key === "github" ? "repository" : "conversation"),
    ),
    [targetId, setTargetId] = useState(initial?.external_target_id ?? ""),
    [agentId, setAgentId] = useState(initial?.agent_id ?? ""),
    [receive, setReceive] = useState(initial?.receive_enabled ?? true),
    [batching, setBatching] = useState<Schema["InputBatchingPolicy"] | null>(
      initial?.input_batching ?? null,
    ),
    [policy, setPolicy] = useState<Record<string, unknown>>(
      initial?.provider_policy ?? {},
    ),
    [override, setOverride] = useState(
      initial?.config_override
        ? JSON.stringify(initial.config_override, null, 2)
        : "",
    );
  const definition = definitions.data?.items.find(
    (item) =>
      item.provider_key === account.provider_key &&
      item.config_version === account.provider_config_version,
  );
  const reload = useMutation({
    mutationFn: async () => {
      if (!basis) return;
      const latest = data(
        await client.http.GET(
          "/api/v1/application-accounts/{account_id}/targets/{target_id}",
          { params: { path: { account_id: account.id, target_id: basis.id } } },
        ),
      );
      setBasis(latest);
      setKind(latest.target_kind);
      setTargetId(latest.external_target_id);
      setAgentId(latest.agent_id ?? "");
      setReceive(latest.receive_enabled ?? true);
      setBatching(latest.input_batching ?? null);
      setPolicy(latest.provider_policy ?? {});
      setOverride(
        latest.config_override
          ? JSON.stringify(latest.config_override, null, 2)
          : "",
      );
    },
  });
  const save = useMutation({
    mutationFn: () => {
      if (definition && Object.keys(policy).length)
        validateSettings(definition.reception_policy_schema, policy);
      const body: Schema["TargetConfig"] = {
        target_kind: kind,
        external_target_id: targetId,
        agent_id: agentId || null,
        receive_enabled: receive,
        input_batching: batching,
        provider_policy: Object.keys(policy).length
          ? jsonObject(JSON.stringify(policy))
          : null,
        config_override: inputOverride(override),
      };
      return basis
        ? client.http
            .PUT(
              "/api/v1/application-accounts/{account_id}/targets/{target_id}",
              {
                params: {
                  path: { account_id: account.id, target_id: basis.id },
                },
                body: { ...body, expected_version: basis.version },
              },
            )
            .then(data)
        : client.http
            .POST("/api/v1/application-accounts/{account_id}/targets", {
              params: {
                path: { account_id: account.id },
                header: commandHeaders(workspace.id, key.forBody(body)),
              },
              body,
            })
            .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["account-targets"] });
      close();
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice
        error={definitions.error ?? options.agents.error ?? reload.error}
      />
      <ChoiceField
        placeholder={t("Select target kind")}
        value={kind}
        className="min-w-0"
        disabled={!!basis}
        onValueChange={(value) =>
          setKind(value === "repository" ? "repository" : "conversation")
        }
        label={t("Target kind")}
        options={
          definition?.target_kinds.map((value) => ({
            value,
            label: t(value === "repository" ? "Repository" : "Conversation"),
          })) ?? []
        }
      />
      <FormField
        className="min-w-0 w-full"
        label={t("External target ID")}
        disabled={!!basis}
      >
        <Input
          required={true}
          value={targetId}
          onChange={(event) => setTargetId(event.target.value)}
          maxLength={2048}
        />
      </FormField>
      <ChoiceField
        placeholder={t("Select agent")}
        value={agentId || "default"}
        className="min-w-0"
        onValueChange={(value) => setAgentId(value === "default" ? "" : value)}
        label={t("Agent")}
        options={[
          { value: "default", label: t("Account default") },
          ...(options.agents.data?.map((item) => ({
            value: item.id,
            label: item.name,
          })) ?? []),
        ]}
      />
      <Label className="flex items-center gap-2">
        <Switch checked={receive} onCheckedChange={setReceive} />
        {t("Receive events")}
      </Label>
      <BatchingFields value={batching} onChange={setBatching} />
      {definition && (
        <DisclosureSection title={<>{t("Provider reception policy")}</>}>
          <SchemaFields
            schema={definition.reception_policy_schema}
            value={policy}
            onChange={setPolicy}
          />
        </DisclosureSection>
      )}
      <DisclosureSection
        defaultOpen={!!save.error}
        title={<>{t("Advanced overrides")}</>}
      >
        <TextAreaField
          label={t("Capability overrides (JSON)")}
          hint={t(
            "Optional model, skill, MCP, and connector selections. Leave empty to inherit.",
          )}
          value={override}
          onChange={setOverride}
          code
        />
      </DisclosureSection>
      <ErrorNotice
        error={save.error}
        retry={basis ? () => reload.mutate() : undefined}
      />
      <FormActions pending={save.isPending} />
    </form>
  );
}
