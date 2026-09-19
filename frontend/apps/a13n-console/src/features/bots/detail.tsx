import {
  ArrowSquareOutIcon,
  ArrowsClockwiseIcon,
  PlugsConnectedIcon,
} from "@phosphor-icons/react";
import { Button, MenuItem } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { DetailHeader, DetailLayout, DetailPage } from "../../shared/page";
import { AccountActions } from "../application-accounts/actions";
import {
  PlatformIcon,
  SetupPill,
  usePlatformName,
} from "../integrations/platform";
import { botAccount } from "./account";
import { BotChannels } from "./channels";
import { useBotCheck, useCheckLabel } from "./checks";
import { BotConversations } from "./conversations";
import { BotMemory } from "./memory";
import { BotOverview } from "./overview";
import { BotSettings } from "./settings";

export { BotsPage } from "./list";

const tabs = ["overview", "channels", "conversations", "memory", "settings"];

/** External home of the installation, when the platform exposes a stable one. */
function platformUrl(account: Schema["Account"], organization?: string | null) {
  const team = account.provider_config?.team_id;
  if (account.provider_key === "slack" && typeof team === "string" && team)
    return `https://app.slack.com/client/${team}`;
  if (account.provider_key === "github" && organization)
    return `https://github.com/${organization}`;
  return undefined;
}

export function BotDetail() {
  const { accountId = "", botTab = "overview" } = useParams(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    platformName = usePlatformName(),
    navigate = useNavigate();
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
    !["slack", "lark", "github"].includes(account.provider_key)
  )
    return (
      <Empty
        title={t("Bot not found")}
        description={t(
          "Choose a Slack, Feishu, or GitHub bot in this workspace.",
        )}
      />
    );
  const github = account.provider_key === "github";
  const tab = botTab === "memory" && github ? "overview" : botTab;
  if (!tabs.includes(tab))
    return (
      <Empty
        title={t("Page not found")}
        description={t("Choose a bot page from the navigation.")}
      />
    );
  const tabPath = (value: string) =>
    `${basePath}/bots/${accountId}${value === "overview" ? "" : `/${value}`}`;
  const channelLabel = github
    ? "Repositories"
    : account.provider_key === "slack"
      ? "Channels"
      : "Groups";
  const organization =
    summary.external_organization_name ?? summary.external_organization_id;
  const external = platformUrl(account, summary.external_organization_name);
  return (
    <DetailPage
      back={`${basePath}/bots`}
      backLabel={t("Bots")}
      tab={tab}
      onTabChange={(value) => navigate(tabPath(value))}
      tabs={[
        { value: "overview", label: t("Overview") },
        {
          value: "channels",
          label: t(channelLabel),
          count: summary.configured_target_count,
        },
        { value: "conversations", label: t("Conversations") },
        ...(github ? [] : [{ value: "memory", label: t("Memory") }]),
        { value: "settings", label: t("Settings") },
      ]}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <PlatformIcon type={account.provider_key} size={24} />
            </IconTile>
          }
          name={account.name}
          status={<SetupPill condition={summary.setup_condition} />}
          description={`${platformName(account.provider_key)} · ${
            organization ?? t("Organization not verified")
          }`}
          actions={
            <>
              {external && (
                <Button
                  variant="outline"
                  render={
                    <a href={external} target="_blank" rel="noreferrer" />
                  }
                >
                  {t("Open {{platform}}", {
                    platform: platformName(account.provider_key),
                  })}
                  <ArrowSquareOutIcon size={13} aria-hidden="true" />
                </Button>
              )}
              <AccountActions
                account={account}
                reload={reload}
                onDeleted={() => navigate(`${basePath}/bots`)}
                label={t("Bot actions")}
                leading={<BotMenuActions account={account} summary={summary} />}
              />
            </>
          }
        />
      }
    >
      {tab === "overview" ? (
        <BotOverview summary={summary} />
      ) : (
        <DetailLayout>
          {tab === "channels" && <BotChannels account={account} />}
          {tab === "conversations" && (
            <BotConversations accountId={account.id} />
          )}
          {tab === "memory" && !github && <BotMemory account={account} />}
          {tab === "settings" && (
            <BotSettings
              key={generation}
              account={account}
              reload={reload}
              onMemoryConfigured={() => navigate(tabPath("memory"))}
              onCancel={() => navigate(tabPath("overview"))}
            />
          )}
        </DetailLayout>
      )}
    </DetailPage>
  );
}

/** Bot-specific entries at the top of the account overflow menu. */
function BotMenuActions({
  account,
  summary,
}: {
  account: Schema["Account"];
  summary: Schema["BotSummary"];
}) {
  const { t } = useTranslation(),
    { basePath, can } = useWorkspace();
  const { check, running } = useBotCheck({ account });
  const label = useCheckLabel();
  if (!can("application_account.manage")) return null;
  return (
    <>
      <MenuItem
        closeOnClick={false}
        disabled={running}
        onClick={() => check.mutate()}
      >
        <PlugsConnectedIcon size={14} />
        {label(check.isPending)}
      </MenuItem>
      {summary.setup_condition !== "receiving" && (
        <MenuItem
          render={
            <Link to={`${basePath}/bots/connect?account=${account.id}`} />
          }
        >
          <ArrowsClockwiseIcon size={14} />
          {t("Resume setup")}
        </MenuItem>
      )}
    </>
  );
}
