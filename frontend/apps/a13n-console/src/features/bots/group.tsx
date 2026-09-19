import { SettingsRow, SettingsSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { ChatCircleDotsIcon, GitBranchIcon } from "@phosphor-icons/react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import {
  DetailHeader,
  DetailLayout,
  DetailPage,
  Section,
} from "../../shared/page";
import { AgentLink } from "../agents/link";
import { TargetEditor } from "../application-accounts/targets";
import {
  messagingPolicy,
  placementLabels,
  automaticPlacementHint,
  responseLabels,
} from "../application-accounts/messaging-fields";
import { ReceptionPill, usePlatformName } from "../integrations/platform";
import { botAccount, type BotAccount } from "./account";
import { useChannelNames } from "./channels";
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
      ["slack", "lark", "github"].includes(account.data.provider_key),
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
    !["slack", "lark", "github"].includes(account.data.provider_key)
  )
    return (
      <Empty
        title={t("Bot not found")}
        description={t(
          "Choose a Slack, Feishu, or GitHub bot in this workspace.",
        )}
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
    current.target_kind !==
      (account.data.provider_key === "github" ? "repository" : "conversation")
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
  const github = account.data.provider_key === "github";
  if (github && groupTab === "memory")
    return (
      <Empty
        title={t("Memory unavailable")}
        description={t("GitHub repository memory is not supported.")}
      />
    );
  const root = `${basePath}/bots/${accountId}/channels/${targetId}`;
  function path(tab: string) {
    return `${root}${tab === "configuration" ? "" : `/${tab}`}${tab === "memory" && search.size ? `?${search}` : ""}`;
  }
  return (
    <ChannelDetail
      account={account.data}
      target={current}
      tab={groupTab}
      onTabChange={(value) => navigate(path(value))}
      memoryPath={path("memory")}
    />
  );
}

function ChannelDetail({
  account,
  target,
  tab,
  onTabChange,
  memoryPath,
}: {
  account: BotAccount;
  target: Schema["AccountTarget"];
  tab: string;
  onTabChange: (value: string) => void;
  memoryPath: string;
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace(),
    platformName = usePlatformName();
  const github = account.provider_key === "github";
  const nameOf = useChannelNames(account);
  const name = nameOf(target.external_target_id);
  const organization =
    account.provider_config?.[
      account.provider_key === "slack" ? "team_id" : "tenant_key"
    ];
  return (
    <DetailPage
      back={`${basePath}/bots/${account.id}/channels`}
      backLabel={`${t("Bots")} / ${account.name}`}
      tab={tab}
      onTabChange={onTabChange}
      tabs={[
        { value: "configuration", label: t("Configuration") },
        { value: "conversations", label: t("Conversations") },
        ...(github ? [] : [{ value: "memory", label: t("Memory") }]),
      ]}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              {github ? (
                <GitBranchIcon aria-hidden="true" size={20} />
              ) : (
                <ChatCircleDotsIcon aria-hidden="true" size={20} />
              )}
            </IconTile>
          }
          name={name}
          status={<ReceptionPill enabled={!!target.receive_enabled} />}
          resourceKey={
            name === target.external_target_id
              ? undefined
              : target.external_target_id
          }
          description={`${platformName(account.provider_key)} · ${
            typeof organization === "string" && organization
              ? organization
              : account.name
          }`}
        />
      }
    >
      <DetailLayout>
        {tab === "configuration" && (
          <>
            <GroupConfiguration
              account={account}
              target={target}
              memoryPath={memoryPath}
            />
            <BotChecks
              account={account}
              conversationId={target.external_target_id}
            />
          </>
        )}
        {tab === "conversations" && (
          <BotConversations accountId={account.id} targetId={target.id} />
        )}
        {tab === "memory" && !github && (
          <BotGroupMemory account={account} target={target} />
        )}
      </DetailLayout>
    </DetailPage>
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
  const github = account.provider_key === "github";
  const policy = messagingPolicy(
      target.provider_policy ?? account.provider_policy,
    ),
    agentId = target.agent_id ?? account.default_agent_id;
  const source = (overridden: boolean) =>
    t(overridden ? "Conversation override" : "Account default");
  return (
    <Section
      title={t("Configuration")}
      description={t(
        "Settings for this exact conversation. Other conversations keep their own settings.",
      )}
      actions={
        can("account_target.manage") && (
          <TargetEditor account={account} target={target} bot />
        )
      }
    >
      <SettingsSection>
        <SettingsRow label={t("Receive messages")}>
          <ReceptionPill enabled={!!target.receive_enabled} />
        </SettingsRow>
        <SettingsRow label={t("Agent")} description={source(!!target.agent_id)}>
          {agentId ? (
            <AgentLink agentId={agentId} />
          ) : (
            <span className={styles.unset}>{t("Not configured")}</span>
          )}
        </SettingsRow>
        <SettingsRow
          label={t("When to respond")}
          description={source(!!target.provider_policy)}
        >
          {github
            ? t(
                account.provider_config_version === "github_notifications_v1"
                  ? "Notification updates"
                  : "Selected GitHub events",
              )
            : policy
              ? t(responseLabels[policy.interaction_mode])
              : t("Platform default")}
        </SettingsRow>
        <SettingsRow
          label={t("Reply placement")}
          description={
            !github && policy?.reply_mode === "auto"
              ? t(automaticPlacementHint)
              : undefined
          }
        >
          {github
            ? t("Issue or PR comment")
            : policy
              ? t(placementLabels[policy.reply_mode])
              : t("Platform default")}
        </SettingsRow>
        <SettingsRow label={t("Advanced overrides")}>
          {t(target.config_override ? "Configured" : "Account default")}
        </SettingsRow>
        {!github && (
          <SettingsRow label={t("Memory policy")}>
            <Link to={memoryPath}>{t("View conversation memory")}</Link>
          </SettingsRow>
        )}
      </SettingsSection>
      {(!account.receive_enabled || account.status !== "active") && (
        <p className={styles.notice} data-tone="warning" role="status">
          {t(
            "Bot reception is disabled. Enabling this conversation alone will not receive messages.",
          )}
        </p>
      )}
      <p className={styles.hint}>
        {t(
          "Routing changes apply to future inputs. Accepted work and active conversations retain their execution configuration.",
        )}
      </p>
    </Section>
  );
}
