import { botAccount } from "./account";
import { Tabs, TabsList, TabsPanel, TabsTab } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Link,
  Navigate,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { AccountForm } from "../application-accounts/form";
import { AccountTargets } from "../application-accounts/targets";
import { AccountCredentials } from "../application-accounts/credentials";
import { BotMemory } from "./memory";
import { BotOverview } from "./overview";
import { BotConversations } from "./conversations";
import styles from "./bots.module.css";

export { BotsPage } from "./collection";

export function BotDetail() {
  const { accountId = "", botTab = "overview" } = useParams(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace();
  const { t } = useTranslation(),
    [search] = useSearchParams(),
    navigate = useNavigate(),
    tab = botTab === "channels" ? "groups" : botTab;
  function tabPath(value: string) {
    const next = new URLSearchParams(search);
    next.delete("tab");
    const suffix =
      value === "overview" ? "" : `/${value === "groups" ? "channels" : value}`;
    return `${basePath}/bots/${accountId}${suffix}${next.size ? `?${next}` : ""}`;
  }
  const [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["application-accounts", workspace.id, accountId, "bot-summary"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/summary", {
          params: { path: { account_id: accountId } },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  if (query.isPending) return <Loading variant="detail" page />;
  if (query.error || !query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const summary = query.data,
    account = botAccount(summary);
  if (
    account.workspace_id !== workspace.id ||
    !["slack", "lark"].includes(account.provider_key)
  )
    return (
      <Empty
        title={t("Bot not found")}
        description={t("Choose a Slack or Feishu bot in this workspace.")}
      />
    );
  if (
    !["overview", "groups", "conversations", "memory", "settings"].includes(tab)
  )
    return (
      <Empty
        title={t("Page not found")}
        description={t("Choose a bot page from the navigation.")}
      />
    );
  const legacyTab = search.get("tab");
  if (
    legacyTab &&
    ["overview", "groups", "conversations", "memory", "settings"].includes(
      legacyTab,
    )
  )
    return <Navigate replace to={tabPath(legacyTab)} />;
  const groupLabel = account.provider_key === "slack" ? "Channels" : "Groups";
  return (
    <Page
      title={account.name}
      back={`${basePath}/bots`}
      description={`${account.provider_key === "slack" ? "Slack" : t("Feishu")} · ${summary.external_organization_name ?? summary.external_organization_id ?? t("Organization not verified")}`}
      actions={<StateBadge state={account.status} />}
    >
      <Tabs
        value={tab}
        onValueChange={(value) => navigate(tabPath(String(value)))}
      >
        <TabsList className={styles.detailTabs} aria-label={t("Bot details")}>
          <TabsTab value="overview">{t("Overview")}</TabsTab>
          <TabsTab value="groups">{t(groupLabel)}</TabsTab>
          <TabsTab value="conversations">{t("Conversations")}</TabsTab>
          <TabsTab value="memory">{t("Memory")}</TabsTab>
          <TabsTab value="settings">{t("Settings")}</TabsTab>
        </TabsList>
        <TabsPanel value="overview">
          {tab === "overview" && <BotOverview summary={summary} />}
        </TabsPanel>
        <TabsPanel value="groups">
          <AccountTargets account={account} bot />
        </TabsPanel>
        <TabsPanel value="conversations">
          {tab === "conversations" && (
            <BotConversations accountId={account.id} />
          )}
        </TabsPanel>
        <TabsPanel value="memory">
          {tab === "memory" && <BotMemory account={account} reload={reload} />}
        </TabsPanel>
        <TabsPanel value="settings">
          <div className={styles.settings} key={generation}>
            {can("application_account.manage") && (
              <AccountForm
                bot
                initial={account}
                reload={reload}
                onCancel={() => navigate(tabPath("overview"))}
                onSuccess={() => void reload()}
              />
            )}
            <AccountCredentials account={account} reload={reload} />
          </div>
        </TabsPanel>
      </Tabs>
    </Page>
  );
}
