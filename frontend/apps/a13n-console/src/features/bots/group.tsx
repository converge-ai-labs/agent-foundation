import { botAccount } from "./account";
import { Tabs, TabsList, TabsPanel, TabsTab } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { AgentLink } from "../agents/link";
import { TargetEditor } from "../application-accounts/targets";
import {
  messagingPolicy,
  placementLabels,
  automaticPlacementHint,
  responseLabels,
} from "../application-accounts/messaging-fields";
import { BotChecks } from "./checks";
import { BotConversations } from "./conversations";
import { BotGroupMemory } from "./memory";
import styles from "./bots.module.css";

export function BotGroupDetail() {
  const {
    accountId = "",
    targetId = "",
    groupTab = "configuration",
  } = useParams();
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate(),
    [search] = useSearchParams();
  const account = useQuery({
    queryKey: ["application-accounts", workspace.id, accountId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/summary", {
          params: { path: { account_id: accountId } },
          signal,
        })
        .then(data)
        .then(botAccount),
  });
  const target = useQuery({
    queryKey: ["account-targets", workspace.id, accountId, targetId],
    enabled:
      account.data?.workspace_id === workspace.id &&
      ["slack", "lark"].includes(account.data.provider_key),
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/targets/{target_id}", {
          params: { path: { account_id: accountId, target_id: targetId } },
          signal,
        })
        .then(data),
  });
  if (account.isPending) return <Loading variant="detail" page />;
  if (account.error)
    return (
      <ErrorNotice error={account.error} retry={() => void account.refetch()} />
    );
  if (
    !account.data ||
    account.data.workspace_id !== workspace.id ||
    !["slack", "lark"].includes(account.data.provider_key)
  )
    return (
      <Empty
        title={t("Bot not found")}
        description={t("Choose a Slack or Feishu bot in this workspace.")}
      />
    );
  if (target.isPending) return <Loading variant="detail" page />;
  if (target.error)
    return (
      <ErrorNotice error={target.error} retry={() => void target.refetch()} />
    );
  const current = target.data;
  if (
    !current ||
    current.account_id !== accountId ||
    current.target_kind !== "conversation"
  )
    return (
      <Empty
        title={t("Conversation not found")}
        description={t("Choose a configured conversation for this bot.")}
      />
    );
  if (!["configuration", "conversations", "memory"].includes(groupTab))
    return (
      <Empty
        title={t("Page not found")}
        description={t("Choose a bot page from the navigation.")}
      />
    );
  const root = `${basePath}/bots/${accountId}/channels/${targetId}`;
  function path(tab: string) {
    return `${root}${tab === "configuration" ? "" : `/${tab}`}${tab === "memory" && search.size ? `?${search}` : ""}`;
  }
  const organization =
    account.data.provider_config[
      account.data.provider_key === "slack" ? "team_id" : "tenant_key"
    ];
  return (
    <Page
      title={current.external_target_id}
      back={`${basePath}/bots/${accountId}/channels`}
      description={`${account.data.provider_key === "slack" ? "Slack" : t("Feishu")} · ${typeof organization === "string" ? organization : t("External organization")}`}
    >
      <nav
        aria-label={t("Conversation location")}
        className={styles.groupBreadcrumb}
      >
        <Link to={`${basePath}/bots/${accountId}`}>{account.data.name}</Link>
        <span aria-hidden="true">/</span>
        <span>{current.external_target_id}</span>
      </nav>
      <Tabs
        value={groupTab}
        onValueChange={(value) => navigate(path(String(value)))}
      >
        <TabsList
          className={styles.detailTabs}
          aria-label={t("Conversation details")}
        >
          <TabsTab value="configuration">{t("Configuration")}</TabsTab>
          <TabsTab value="conversations">{t("Conversations")}</TabsTab>
          <TabsTab value="memory">{t("Memory")}</TabsTab>
        </TabsList>
        <TabsPanel value="configuration">
          {groupTab === "configuration" && (
            <>
              <GroupConfiguration
                account={account.data}
                target={current}
                memoryPath={path("memory")}
              />
              <BotChecks
                account={account.data}
                conversationId={current.external_target_id}
              />
            </>
          )}
        </TabsPanel>
        <TabsPanel value="conversations">
          {groupTab === "conversations" && (
            <BotConversations accountId={accountId} targetId={targetId} />
          )}
        </TabsPanel>
        <TabsPanel value="memory">
          {groupTab === "memory" && (
            <BotGroupMemory account={account.data} target={current} />
          )}
        </TabsPanel>
      </Tabs>
    </Page>
  );
}

function GroupConfiguration({
  account,
  target,
  memoryPath,
}: {
  account: Schema["Account"];
  target: Schema["AccountTarget"];
  memoryPath: string;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  const policy = messagingPolicy(
      target.provider_policy ?? account.provider_policy,
    ),
    agentId = target.agent_id ?? account.default_agent_id;
  return (
    <section className={styles.groupConfiguration}>
      <div className={styles.memoryHeading}>
        <div>
          <h2>{t("Configuration")}</h2>
          <p>
            {t(
              "Settings for this exact conversation. Other conversations keep their own settings.",
            )}
          </p>
        </div>
        {can("account_target.manage") && (
          <TargetEditor account={account} target={target} bot />
        )}
      </div>
      <dl className={styles.groupFacts}>
        <div>
          <dt>{t("Receive messages")}</dt>
          <dd>
            <StateBadge
              state={target.receive_enabled ? "enabled" : "disabled"}
            />
          </dd>
        </div>
        <div>
          <dt>{t("Agent")}</dt>
          <dd>
            {agentId ? <AgentLink agentId={agentId} /> : t("Not configured")}
            <small>
              {t(target.agent_id ? "Conversation override" : "Account default")}
            </small>
          </dd>
        </div>
        <div>
          <dt>{t("When to respond")}</dt>
          <dd>
            {policy
              ? t(responseLabels[policy.interaction_mode])
              : t("Platform default")}
            <small>
              {t(
                target.provider_policy
                  ? "Conversation override"
                  : "Account default",
              )}
            </small>
          </dd>
        </div>
        <div>
          <dt>{t("Reply placement")}</dt>
          <dd>
            {policy
              ? t(placementLabels[policy.reply_mode])
              : t("Platform default")}
            {policy?.reply_mode === "auto" && (
              <small>{t(automaticPlacementHint)}</small>
            )}
          </dd>
        </div>
        <div>
          <dt>{t("Advanced overrides")}</dt>
          <dd>
            {t(target.config_override ? "Configured" : "Account default")}
          </dd>
        </div>
        <div>
          <dt>{t("Memory policy")}</dt>
          <dd>
            <Link to={memoryPath}>{t("View conversation memory")}</Link>
          </dd>
        </div>
      </dl>
      {(!account.receive_enabled || account.status !== "active") && (
        <p role="status">
          {t(
            "Bot reception is disabled. Enabling this conversation alone will not receive messages.",
          )}
        </p>
      )}
      <p>
        {t(
          "Routing changes apply to future inputs. Accepted work and active conversations retain their execution configuration.",
        )}
      </p>
    </section>
  );
}
