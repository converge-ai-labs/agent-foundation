import { Button, ChoiceField, MenuItem } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowsClockwiseIcon, RobotIcon } from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page } from "../../shared/page";
import { AgentLink } from "../agents/link";
import {
  PlatformIcon,
  SetupPill,
  setupConditions,
  usePlatformName,
  type SetupCondition,
} from "../integrations/platform";
import styles from "./bots.module.css";

export function BotsPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    platformName = usePlatformName(),
    navigate = useNavigate();
  const page = useCursor();
  const [platform, setPlatform] = useState<"slack" | "lark" | "github" | "">(
    "",
  );
  const [condition, setCondition] = useState<SetupCondition | "">("");
  const [draft, setDraft] = useState(""),
    [search, setSearch] = useState("");
  // The collection is queried on the server; typing settles before it asks.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch((current) => {
        const next = draft.trim();
        if (next !== current) page.reset();
        return next;
      });
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);
  const filtered = Boolean(platform || condition || search);
  const query = useQuery({
    queryKey: ["bots", workspace.id, platform, condition, search, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/bots", {
          params: {
            path: { workspace: workspace.id },
            query: {
              platform: platform || undefined,
              condition: condition || undefined,
              search: search || undefined,
              cursor: page.cursor,
            },
          },
          signal,
        })
        .then(data),
  });
  function clearFilters() {
    setPlatform("");
    setCondition("");
    setDraft("");
    setSearch("");
    page.reset();
  }
  const connect = can("application_account.manage") && (
    <Button render={<Link to={`${basePath}/bots/connect`} />}>
      {t("Connect a bot")}
    </Button>
  );
  const items =
    query.data?.items.map((item) => ({ ...item, id: item.account.id })) ?? [];
  return (
    <Page
      title={t("Bots")}
      description={t(
        "Bring an agent into Slack, Feishu, and GitHub conversations.",
      )}
      actions={connect}
      toolbar={
        <Toolbar
          search={draft}
          onSearchChange={setDraft}
          searchLabel={t("Search bots")}
          filters={
            <>
              <ChoiceField
                label={t("Platform")}
                variant="filter"
                value={platform}
                options={[
                  { value: "", label: t("All platforms") },
                  { value: "slack", label: "Slack" },
                  { value: "lark", label: t("Feishu") },
                  { value: "github", label: "GitHub" },
                ]}
                onValueChange={(value) => {
                  if (
                    value === "" ||
                    value === "slack" ||
                    value === "lark" ||
                    value === "github"
                  ) {
                    setPlatform(value);
                    page.reset();
                  }
                }}
              />
              <ChoiceField
                label={t("Setup")}
                variant="filter"
                value={condition}
                options={[
                  { value: "", label: t("Any setup state") },
                  ...Object.entries(setupConditions).map(([value, label]) => ({
                    value,
                    label: t(label),
                  })),
                ]}
                onValueChange={(value) => {
                  if (value === "" || Object.hasOwn(setupConditions, value)) {
                    setCondition(value as SetupCondition | "");
                    page.reset();
                  }
                }}
              />
              {filtered && (
                <Button type="button" variant="ghost" onClick={clearFilters}>
                  {t("Clear filters")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : (
        !query.error &&
        (items.length ? (
          <>
            <ResourceTable
              items={items}
              caption={t("Bots")}
              onRowActivate={(item) => navigate(`${basePath}/bots/${item.id}`)}
              rowMenuLabel={t("Bot actions")}
              rowMenu={(item) =>
                can("application_account.manage") &&
                item.setup_condition !== "receiving" ? (
                  <MenuItem
                    render={
                      <Link
                        to={`${basePath}/bots/connect?account=${item.id}`}
                      />
                    }
                  >
                    <ArrowsClockwiseIcon size={14} />
                    {t("Resume setup")}
                  </MenuItem>
                ) : null
              }
              columns={[
                {
                  label: t("Bot"),
                  tone: "primary",
                  render: ({
                    account,
                    external_organization_name,
                    external_organization_id,
                  }) => (
                    <ResourceIdentity
                      to={`${basePath}/bots/${account.id}`}
                      name={account.name}
                      icon={<PlatformIcon type={account.provider_key} />}
                      description={`${platformName(account.provider_key)} · ${
                        external_organization_name ??
                        external_organization_id ??
                        t("Organization not verified")
                      }`}
                      resourceId={account.id}
                    />
                  ),
                },
                {
                  label: t("Agent"),
                  render: ({ account }) =>
                    account.default_agent_id ? (
                      <AgentLink agentId={account.default_agent_id} />
                    ) : (
                      <span className={styles.unset}>
                        {t("Not configured")}
                      </span>
                    ),
                },
                {
                  label: t("Setup"),
                  render: (item) => (
                    <SetupPill condition={item.setup_condition} />
                  ),
                },
                {
                  label: t("Conversations"),
                  render: (item) => (
                    <Link to={`${basePath}/bots/${item.id}/channels`}>
                      {item.configured_target_count}
                    </Link>
                  ),
                },
                {
                  label: t("Updated"),
                  tone: "muted",
                  render: ({ account }) => (
                    <Timestamp value={account.updated_at} relative />
                  ),
                },
              ]}
            />
            <CollectionFooter
              count={t("{{count}} bots on this page", { count: items.length })}
            >
              <Pagination page={page} next={query.data?.next_cursor} />
            </CollectionFooter>
          </>
        ) : (
          <Empty
            icon={<RobotIcon aria-hidden="true" />}
            title={t(filtered ? "No matching bots" : "No bots connected")}
            description={t(
              filtered
                ? "Change or clear the filters to find your bot."
                : "Connect a Slack, Feishu, or GitHub identity, select an agent, and choose where it can respond.",
            )}
            action={!filtered && connect}
          />
        ))
      )}
    </Page>
  );
}
