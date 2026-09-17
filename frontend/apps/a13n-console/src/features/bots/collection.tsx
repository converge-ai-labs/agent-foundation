import { RobotIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, FormField, Input } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { AgentLink } from "../agents/link";
import styles from "./bots.module.css";

import { conditions, stages, type Condition } from "./summary-labels";

export function BotsPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const page = useCursor();
  const [platform, setPlatform] = useState<"slack" | "lark" | "github" | "">(
    "",
  );
  const [condition, setCondition] = useState<Condition | "">("");
  const [draft, setDraft] = useState(""),
    [search, setSearch] = useState("");
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
  return (
    <Page
      title={t("Bots")}
      description={t(
        "Bring an agent into Slack, Feishu, and GitHub conversations.",
      )}
      actions={
        can("application_account.manage") && (
          <Button render={<Link to={`${basePath}/bots/connect`} />}>
            {t("Connect a bot")}
          </Button>
        )
      }
    >
      <form
        className={styles.collectionFilters}
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(draft.trim());
          page.reset();
        }}
      >
        <FormField label={t("Search bots")}>
          <Input
            value={draft}
            maxLength={256}
            placeholder={t("Bot name or external organization")}
            onChange={(event) => setDraft(event.target.value)}
          />
        </FormField>
        <ChoiceField
          label={t("Platform")}
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
          label={t("Setup condition")}
          value={condition}
          options={[
            { value: "", label: t("All conditions") },
            ...Object.entries(conditions).map(([value, label]) => ({
              value,
              label: t(label),
            })),
          ]}
          onValueChange={(value) => {
            if (value === "" || Object.hasOwn(conditions, value)) {
              setCondition(value as Condition | "");
              page.reset();
            }
          }}
        />
        <Button type="submit" variant="outline">
          {t("Search")}
        </Button>
        {filtered && (
          <Button type="button" variant="ghost" onClick={clearFilters}>
            {t("Clear filters")}
          </Button>
        )}
      </form>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : (
        !query.error &&
        (query.data?.items.length ? (
          <>
            <ResourceTable
              items={query.data.items.map((item) => ({
                ...item,
                id: item.account.id,
              }))}
              onRowActivate={(item) => navigate(`${basePath}/bots/${item.id}`)}
              columns={[
                {
                  label: t("Bot"),
                  tone: "primary",
                  render: ({
                    account,
                    external_organization_name,
                    external_organization_id,
                  }) => (
                    <Link
                      to={`${basePath}/bots/${account.id}`}
                      className={styles.identity}
                    >
                      <span className={styles.avatar}>
                        <RobotIcon aria-hidden="true" size={22} />
                      </span>
                      <span className={styles.collectionIdentity}>
                        <strong>{account.name}</strong>
                        <small>
                          {account.provider_key === "github"
                            ? "GitHub"
                            : account.provider_key === "slack"
                              ? "Slack"
                              : t("Feishu")}
                          {" · "}
                          {external_organization_name ??
                            external_organization_id ??
                            t("Organization not verified")}
                        </small>
                        {external_organization_name && (
                          <small>{external_organization_id}</small>
                        )}
                      </span>
                    </Link>
                  ),
                },
                {
                  label: t("Availability"),
                  render: ({ account }) => (
                    <StateBadge state={account.status} />
                  ),
                },
                {
                  label: t("Setup and test"),
                  render: (item) => (
                    <div className={styles.collectionObservation}>
                      <span>{t(conditions[item.setup_condition])}</span>
                      <small>
                        {t(
                          item.test_stage
                            ? stages[item.test_stage]
                            : "No setup test recorded",
                        )}
                      </small>
                      {(item.test_observed_at || item.checked_at) && (
                        <time
                          dateTime={(item.test_observed_at || item.checked_at)!}
                        >
                          {t(
                            item.test_observed_at
                              ? "Test observation"
                              : "Last checked",
                          )}
                          {" · "}
                          {new Date(
                            (item.test_observed_at || item.checked_at)!,
                          ).toLocaleString()}
                        </time>
                      )}
                      {can("application_account.manage") &&
                        item.setup_condition !== "receiving" && (
                          <Link
                            to={`${basePath}/bots/connect?account=${item.id}`}
                          >
                            {t("Resume setup")}
                          </Link>
                        )}
                    </div>
                  ),
                },
                {
                  label: t("Configured conversations"),
                  render: (item) => (
                    <Link to={`${basePath}/bots/${item.id}/channels`}>
                      {item.configured_target_count}
                    </Link>
                  ),
                },
                {
                  label: t("Default agent"),
                  render: ({ account }) =>
                    account.default_agent_id ? (
                      <AgentLink agentId={account.default_agent_id} />
                    ) : (
                      t("Not configured")
                    ),
                },
              ]}
            />
            <Pagination page={page} next={query.data.next_cursor} />
            <p className={styles.collectionNote}>
              {t(
                "Configured conversations count only saved targets. Verification and test results are past observations, not a live health check.",
              )}
            </p>
          </>
        ) : (
          <Empty
            title={t(filtered ? "No matching bots" : "No bots connected")}
            description={t(
              filtered
                ? "Change or clear the filters to find your bot."
                : "Connect a Slack, Feishu, or GitHub identity, select an agent, and choose where it can respond.",
            )}
          />
        ))
      )}
    </Page>
  );
}
