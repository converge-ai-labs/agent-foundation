import { Button, ChoiceField } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import { useReceptionOptions } from "../application-accounts/data";
import {
  automaticPlacementHint,
  placementLabels,
} from "../application-accounts/messaging-fields";
import { ConversationPicker } from "./conversation-picker";
import { GitHubPolicyFields } from "./github-policy";
import { BotChecks } from "./checks";
import styles from "./connect.module.css";

export function BotPilot({
  account: initial,
  onSuccess,
  onBack,
  reload,
}: {
  account: Schema["Account"];
  onSuccess: (
    account: Schema["Account"],
    target: Schema["AccountTarget"],
  ) => void;
  onBack: () => void;
  reload: () => Promise<void>;
}) {
  const [account] = useState(initial);
  const github = account.provider_key === "github";
  const targetKind: "repository" | "conversation" = github
    ? "repository"
    : "conversation";
  const [githubPolicy, setGithubPolicy] = useState<
    Schema["GitHubReceptionPolicy"]
  >({ allowed_senders: ["*"], event_actions: [] });
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const key = useIdempotency(),
    options = useReceptionOptions(true);
  const [conversationId, setConversationId] = useState("");
  const [agentId, setAgentId] = useState(account.default_agent_id ?? ""),
    [executionId, setExecutionId] = useState(
      account.execution_service_account_id ?? "",
    ),
    [interaction, setInteraction] = useState<
      Schema["MessagingPolicy"]["interaction_mode"]
    >(
      account.provider_policy?.interaction_mode === "chat"
        ? "chat"
        : account.provider_policy?.interaction_mode === "discussion"
          ? "discussion"
          : "mention",
    ),
    [reply, setReply] = useState<Schema["MessagingPolicy"]["reply_mode"]>(
      account.provider_policy?.reply_mode === "main"
        ? "main"
        : account.provider_policy?.reply_mode === "thread"
          ? "thread"
          : "auto",
    );
  const targets = useQuery({
    queryKey: ["account-targets", workspace.id, account.id, "pilot"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/application-accounts/{account_id}/targets", {
            params: {
              path: { account_id: account.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const existingSelection = targets.data?.find(
    (item) =>
      item.target_kind === targetKind &&
      item.external_target_id === conversationId.trim(),
  );
  const enabled =
    targets.data?.filter((target) => target.receive_enabled) ?? [];
  const candidate = enabled.length === 1 ? enabled[0] : undefined;
  const [basis, setBasis] = useState<Schema["AccountTarget"] | null>(null);
  if (!basis && candidate) setBasis(candidate);
  const target = basis ?? candidate;
  const targetChanged =
    !!basis &&
    (candidate?.id !== basis.id || candidate?.version !== basis.version);
  const eligible =
    enabled.length === 1 &&
    candidate?.id === target?.id &&
    candidate?.version === target?.version &&
    target?.target_kind === targetKind &&
    !target.agent_id &&
    !target.config_override &&
    !target.provider_policy;
  const activate = useMutation({
    mutationFn: async () => {
      if (!target || !eligible)
        throw new Error(
          t(
            "Configure exactly one pilot conversation that inherits its account settings.",
          ),
        );
      return client.http
        .POST("/api/v1/application-accounts/{account_id}/bot/activate", {
          params: { path: { account_id: account.id } },
          body: {
            expected_version: account.version,
            target_id: target.id,
            target_version: target.version,
            conversation_id: target.external_target_id,
            agent_id: agentId,
            execution_service_account_id: executionId,
            policy: github
              ? githubPolicy
              : { interaction_mode: interaction, reply_mode: reply },
          },
        })
        .then(data);
    },
    onSuccess: async (result) => {
      await cache.invalidateQueries({ queryKey: ["bots"] });
      await cache.invalidateQueries({ queryKey: ["application-accounts"] });
      if (target) onSuccess(result, target);
    },
  });
  const create = useMutation({
    mutationFn: () => {
      const body: Schema["TargetConfig"] = {
        target_kind: targetKind,
        external_target_id: conversationId.trim(),
        receive_enabled: true,
      };
      return client.http
        .POST("/api/v1/application-accounts/{account_id}/targets", {
          params: {
            path: { account_id: account.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["account-targets"] });
    },
  });
  if (targets.isPending) return <Loading variant="form" rows={5} />;
  if (
    account.receive_enabled ||
    account.reception_scope !== "configured_targets" ||
    account.status !== "active"
  )
    return (
      <section>
        <h2>{t("Review existing reception settings")}</h2>
        <p>
          {t(
            "This account is already receiving messages or uses broader routing. Review its existing configuration before starting a single-conversation pilot.",
          )}
        </p>
        <Link to={`${basePath}/bots/${account.id}/settings`}>
          {t("Review reception settings")}
        </Link>
        <p>
          <Button variant="outline" onClick={onBack}>
            {t("Back")}
          </Button>
        </p>
      </section>
    );
  return (
    <section>
      <h2>
        {t(
          github ? "Choose a pilot repository" : "Choose a pilot conversation",
        )}
      </h2>
      <p>
        {t(
          github
            ? "Select one repository accessible to this GitHub identity. Reception stays off until you verify and enable it."
            : "Invite the bot to one pilot channel or group. Reception stays off until you explicitly verify and enable it below.",
        )}
      </p>
      <ErrorNotice error={targets.error} retry={() => void targets.refetch()} />
      {targetChanged && (
        <p role="status">
          {t(
            "The pilot configuration changed. Reload to review the current account and conversation before enabling reception.",
          )}
          <Button type="button" variant="outline" onClick={() => void reload()}>
            {t("Reload configuration")}
          </Button>
        </p>
      )}
      {!targets.error && enabled.length === 0 && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
          className={styles.pilotForm}
        >
          <ConversationPicker
            account={account}
            value={conversationId}
            onChange={setConversationId}
            disabled={create.isPending}
          />
          <ErrorNotice
            error={create.error}
            retry={() => void targets.refetch()}
          />
          {existingSelection && (
            <p>
              {t(
                "This conversation is already configured. Review its existing reception setting instead of creating it again.",
              )}
              <Link to={`${basePath}/bots/${account.id}/channels`}>
                {t("Review conversations")}
              </Link>
            </p>
          )}
          <Button
            type="submit"
            disabled={
              !conversationId.trim() || create.isPending || !!existingSelection
            }
          >
            {t(create.isPending ? "Saving…" : "Save pilot conversation")}
          </Button>
        </form>
      )}
      {!targets.error && enabled.length > 0 && !eligible && (
        <p role="status">
          {t(
            "Configure exactly one pilot conversation that inherits its account settings.",
          )}{" "}
          <Link to={`${basePath}/bots/${account.id}/channels`}>
            {t("Review conversations")}
          </Link>
        </p>
      )}
      {target && eligible && (
        <>
          <p>
            {t("Pilot conversation")}: <code>{target.external_target_id}</code>
          </p>
          <BotChecks
            account={account}
            conversationId={target.external_target_id}
          />
          <form
            className={styles.pilotForm}
            onSubmit={(event) => {
              event.preventDefault();
              activate.mutate();
            }}
          >
            <h2>{t("Agent and reception")}</h2>
            <ErrorNotice
              error={options.agents.error ?? options.accounts.error}
            />
            <ChoiceField
              label={t("Default agent")}
              placeholder={t("Select agent")}
              value={agentId}
              onValueChange={setAgentId}
              required
              disabled={activate.isPending}
              options={
                options.agents.data?.map((agent) => ({
                  value: agent.id,
                  label: agent.name,
                })) ?? []
              }
            />
            <p>
              <Link to={`${basePath}/agents`}>
                {t("Open agents in a new tab")}
              </Link>
            </p>
            <ChoiceField
              label={t("Execution service account")}
              placeholder={t("Select service account")}
              value={executionId}
              onValueChange={setExecutionId}
              required
              disabled={activate.isPending}
              options={
                options.accounts.data
                  ?.filter((item) => item.status === "active")
                  .map((item) => ({ value: item.id, label: item.name })) ?? []
              }
            />
            <p>
              {t(
                "The execution service account determines what incoming messages may do. It does not use your browser login or the sender's permissions.",
              )}
            </p>
            {github ? (
              <GitHubPolicyFields
                value={githubPolicy}
                onChange={setGithubPolicy}
                polling={
                  account.provider_config_version === "github_notifications_v1"
                }
              />
            ) : (
              <>
                <ChoiceField
                  label={t("When to respond")}
                  value={interaction}
                  onValueChange={(value) =>
                    setInteraction(value as typeof interaction)
                  }
                  disabled={activate.isPending}
                  options={[
                    { value: "mention", label: t("Only when mentioned") },
                    {
                      value: "discussion",
                      label: t("Continue an activated discussion"),
                    },
                    { value: "chat", label: t("All supported human messages") },
                  ]}
                />
                <p>
                  {t(
                    "Discussion and whole-chat modes need the corresponding message subscriptions and history permissions on the provider.",
                  )}
                </p>
                <ChoiceField
                  label={t("Reply placement")}
                  value={reply}
                  onValueChange={(value) => setReply(value as typeof reply)}
                  disabled={activate.isPending}
                  options={[
                    { value: "auto", label: t(placementLabels.auto) },
                    { value: "thread", label: t("Thread or topic") },
                    { value: "main", label: t("Main conversation") },
                  ]}
                />
                {reply !== "thread" && (
                  <p>
                    {t(
                      reply === "main"
                        ? "Replies in the main conversation may be visible beyond the original discussion."
                        : automaticPlacementHint,
                    )}
                  </p>
                )}
              </>
            )}
            <p>
              {t(
                "Enabling reception rechecks the app and pilot membership. Future matching messages can invoke the selected agent and consume model usage.",
              )}
            </p>
            <ErrorNotice error={activate.error} retry={() => void reload()} />
            <div className={styles.actions}>
              <Button
                type="button"
                variant="outline"
                disabled={activate.isPending}
                onClick={onBack}
              >
                {t("Back")}
              </Button>
              <Button
                type="submit"
                disabled={
                  !agentId ||
                  !executionId ||
                  !!targets.error ||
                  targets.isFetching ||
                  activate.isPending ||
                  !!options.agents.error ||
                  !!options.accounts.error
                }
              >
                {t(
                  activate.isPending
                    ? "Verifying and enabling…"
                    : "Verify and enable reception",
                )}
              </Button>
            </div>
          </form>
        </>
      )}
    </section>
  );
}
