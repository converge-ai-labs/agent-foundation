import { BrandIcon, Button, Tabs, TabsList, TabsPanel, TabsTab } from "a13n-ui";
import { CaretRightIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { CopyButton } from "../../shared/copy";
import { Empty, ErrorNotice, Loading, Page } from "../../shared/feedback";
import { AccountForm } from "../application-accounts/form";
import { EventConnection } from "./event-connection";
import { BotPilot } from "./pilot";
import { PilotTest } from "./pilot-test";
import { BotChecks } from "./checks";
import { BotSetupInstructions } from "./setup-instructions";
import styles from "./connect.module.css";

const steps = [
  "Platform and account",
  "Connect",
  "Verify",
  "Agent and reception",
  "Test",
];

export function BotConnect() {
  const { can } = useWorkspace(),
    { t } = useTranslation();
  if (!can("application_account.manage"))
    return (
      <Empty
        title={t("Administrator access required")}
        description={t("Only workspace administrators can connect bots.")}
      />
    );
  return <ConnectFlow />;
}

function ConnectFlow() {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const accountId = search.get("account") ?? "";
  const [platform, setPlatform] = useState<"slack" | "lark" | "github" | null>(
    null,
  );
  const [accountMode, setAccountMode] = useState<string | null>(null);
  const accounts = useQuery({
    queryKey: ["bot-setup-existing", workspace.id, platform],
    enabled: !!platform && !accountId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/bots", {
          params: {
            path: { workspace: workspace.id },
            query: { limit: 20, platform: platform! },
          },
          signal,
        })
        .then(data),
  });
  const mode =
    accountMode ?? (accounts.data?.items.length ? "existing" : "new");
  const [githubMode, setGithubMode] = useState<"github_polling" | "github">(
    "github_polling",
  );
  const setupProvider =
    platform === "github" ? githubMode : (platform ?? undefined);
  const [pilotGeneration, setPilotGeneration] = useState(0);
  const account = useQuery({
    queryKey: ["application-accounts", workspace.id, accountId],
    enabled: !!accountId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}", {
          params: { path: { account_id: accountId } },
          signal,
        })
        .then(data),
  });
  const current = account.data;
  const check = useQuery({
    queryKey: [
      "bot-check",
      workspace.id,
      accountId,
      current?.credential_generation,
      null,
    ],
    enabled:
      !!current &&
      current.workspace_id === workspace.id &&
      ["slack", "lark", "github"].includes(current.provider_key),
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/checks/latest", {
          params: { path: { account_id: accountId } },
          signal,
        })
        .then(data),
  });
  const latest = check.data?.latest;
  const verified =
    !check.error &&
    latest?.credential_generation === current?.credential_generation &&
    latest?.installation?.enabled === true &&
    !latest.error_code;
  const requested = search.get("step");
  const step = !accountId
    ? platform && accounts.isSuccess && mode === "new"
      ? 1
      : 0
    : !verified
      ? 2
      : requested === "test"
        ? 4
        : requested === "reception"
          ? 3
          : 2;
  function advance(value: "verify" | "reception" | "test") {
    setSearch({ account: accountId, step: value });
  }
  function saved(value: Schema["Account"]) {
    setSearch({ account: value.id, step: "verify" }, { replace: true });
  }
  return (
    <Page
      title={t("Connect a bot")}
      back={`${basePath}/bots`}
      description={t(
        "Connect a platform identity you own, then choose an agent and reception scope.",
      )}
    >
      <div className={styles.layout}>
        <ol className={styles.steps} aria-label={t("Bot setup steps")}>
          {steps.map((label, index) => (
            <li key={label} aria-current={index === step ? "step" : undefined}>
              <span>{index + 1}</span>
              <strong>{t(label)}</strong>
            </li>
          ))}
        </ol>
        <div className={styles.content}>
          {!accountId && !platform && (
            <section>
              <h2>{t("Choose your platform")}</h2>
              <p>{t("Where will your bot respond?")}</p>
              <div className={styles.platforms}>
                <Button variant="outline" onClick={() => setPlatform("slack")}>
                  <BrandIcon alias="slack" />
                  <span>Slack</span>
                  <CaretRightIcon aria-hidden="true" />
                </Button>
                <Button variant="outline" onClick={() => setPlatform("lark")}>
                  <BrandIcon alias="feishu" />
                  <span>{t("Feishu")}</span>
                  <CaretRightIcon aria-hidden="true" />
                </Button>
                <Button variant="outline" onClick={() => setPlatform("github")}>
                  <BrandIcon alias="github" />
                  <span>GitHub</span>
                  <CaretRightIcon aria-hidden="true" />
                </Button>
              </div>
            </section>
          )}
          {!accountId && platform && (
            <>
              <div className={styles.selectedPlatform}>
                <strong>
                  <BrandIcon
                    alias={platform === "lark" ? "feishu" : platform}
                  />
                  {platform === "lark"
                    ? t("Feishu")
                    : platform === "github"
                      ? "GitHub"
                      : "Slack"}
                </strong>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setPlatform(null);
                    setAccountMode(null);
                    setGithubMode("github_polling");
                  }}
                >
                  {t("Change platform")}
                </Button>
              </div>
              <h2>{t("Choose an account")}</h2>
              <ErrorNotice
                error={accounts.error}
                retry={() => void accounts.refetch()}
              />
              {accounts.isPending && <Loading />}
              {accounts.isSuccess && (
                <Tabs
                  value={mode}
                  onValueChange={(value) => setAccountMode(String(value))}
                >
                  <TabsList
                    className={styles.accountModes}
                    aria-label={t("Account source")}
                  >
                    <TabsTab value="existing">
                      {t("Use an existing account")}
                    </TabsTab>
                    <TabsTab value="new">{t("Create a new account")}</TabsTab>
                  </TabsList>
                  <TabsPanel value="existing" className={styles.modeContent}>
                    {mode === "existing" && (
                      <ExistingAccounts
                        accounts={accounts.data}
                        onCreate={() => setAccountMode("new")}
                      />
                    )}
                  </TabsPanel>
                  <TabsPanel value="new" className={styles.modeContent}>
                    {mode === "new" && (
                      <>
                        {accounts.data.items.length === 0 && (
                          <p>
                            {t(
                              "No accounts for this platform yet. Create one to connect your bot.",
                            )}
                          </p>
                        )}
                        {platform === "github" && (
                          <div
                            className={styles.githubModes}
                            role="group"
                            aria-label={t("GitHub connection type")}
                          >
                            <Button
                              variant="outline"
                              aria-pressed={githubMode === "github_polling"}
                              onClick={() => setGithubMode("github_polling")}
                            >
                              <strong>{t("GitHub account · Polling")}</strong>
                              <span>
                                {t(
                                  "Use an account token. No public callback needed.",
                                )}
                              </span>
                            </Button>
                            <Button
                              variant="outline"
                              aria-pressed={githubMode === "github"}
                              onClick={() => setGithubMode("github")}
                            >
                              <strong>{t("GitHub App · Webhook")}</strong>
                              <span>
                                {t(
                                  "Use an installed App and a public webhook address.",
                                )}
                              </span>
                            </Button>
                          </div>
                        )}
                        {platform === "github" ? (
                          <section>
                            <h2>
                              {t(
                                setupProvider === "github_polling"
                                  ? "Connect a GitHub account"
                                  : "Connect a GitHub App",
                              )}
                            </h2>
                            <p>
                              {t(
                                setupProvider === "github_polling"
                                  ? "Use a dedicated GitHub account and a classic PAT: notifications plus public_repo for public repositories, or repo for private repositories. No public callback address is needed."
                                  : "Create and install a GitHub App with Issues and Pull requests read/write permissions. Subscribe to Issues, Issue comments, Pull requests, Pull request reviews, and Pull request review comments. A public webhook address is required.",
                              )}
                            </p>
                            <a
                              href={
                                setupProvider === "github_polling"
                                  ? "https://github.com/settings/tokens"
                                  : "https://github.com/settings/apps"
                              }
                              target="_blank"
                              rel="noreferrer"
                            >
                              {t("Open GitHub settings")}
                            </a>
                          </section>
                        ) : (
                          <BotSetupInstructions platform={platform} />
                        )}
                        <p>
                          {t(
                            "The account is saved with reception disabled. Only explicitly configured conversations will be admitted when you enable it.",
                          )}
                        </p>
                        <p>
                          {t(
                            setupProvider === "github_polling"
                              ? "The token identifies your account automatically. Grant the bot access to the repositories it should handle, and mention or subscribe it to receive notifications."
                              : platform === "lark"
                                ? "Enter your App ID and credentials. We automatically identify your Feishu enterprise and bot before saving."
                                : "Enter the installation identifiers from your app settings. They are verified in the next step; entering an ID does not prove access.",
                          )}
                        </p>
                        <AccountForm
                          key={setupProvider}
                          bot
                          setupProvider={setupProvider}
                          onCancel={() => setAccountMode("existing")}
                          onSuccess={saved}
                        />
                      </>
                    )}
                  </TabsPanel>
                </Tabs>
              )}
            </>
          )}
          {accountId && account.isPending && (
            <Loading variant="form" rows={5} />
          )}
          {accountId && (
            <ErrorNotice
              error={account.error}
              retry={() => void account.refetch()}
            />
          )}
          {current &&
          (current.workspace_id !== workspace.id ||
            !["slack", "lark", "github"].includes(current.provider_key)) ? (
            <Empty
              title={t("Bot not found")}
              description={t(
                "Choose a Slack, Feishu, or GitHub bot in this workspace.",
              )}
            />
          ) : (
            current && (
              <>
                <p className={styles.account}>
                  {current.name} ·{" "}
                  {current.provider_key === "github"
                    ? "GitHub"
                    : current.provider_key === "slack"
                      ? "Slack"
                      : t("Feishu")}
                </p>
                {step === 2 && (
                  <>
                    <CallbackSetup account={current} />
                    <BotChecks account={current} />
                    <p>
                      {t(
                        "Confirm that the verified external organization is the installation you intend to connect.",
                      )}
                    </p>
                    <Button
                      disabled={!verified || check.isFetching}
                      onClick={() => advance("reception")}
                    >
                      {t("Confirm installation and continue")}
                    </Button>
                    <p>
                      <Link
                        to={`${basePath}/application-accounts/${current.id}`}
                      >
                        {t("Review account identity and credentials")}
                      </Link>
                    </p>
                  </>
                )}
                {step === 3 && (
                  <BotPilot
                    key={`${current.id}:${pilotGeneration}`}
                    account={current}
                    reload={async () => {
                      await account.refetch();
                      setPilotGeneration((value) => value + 1);
                    }}
                    onBack={() => advance("verify")}
                    onSuccess={async (_result, target) => {
                      await account.refetch();
                      setSearch({
                        account: current.id,
                        step: "test",
                        pilot: target.external_target_id,
                      });
                    }}
                  />
                )}
                {step === 4 && (
                  <PilotTest
                    account={current}
                    back={() => advance("reception")}
                  />
                )}
              </>
            )
          )}
        </div>
      </div>
    </Page>
  );
}

function ExistingAccounts({
  accounts,
  onCreate,
}: {
  accounts: Schema["BotCollection"];
  onCreate: () => void;
}) {
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  return (
    <section>
      <p>
        {t("Select an account for this platform in the current workspace.")}
      </p>
      <ul className={styles.existing}>
        {accounts.items.map(({ account: item }) => {
          const organization =
            item.provider_config[
              item.provider_key === "slack" ? "team_id" : "tenant_key"
            ];
          return (
            <li key={item.id}>
              <Link to={`${basePath}/bots/${item.id}`}>
                <span>
                  <strong>{item.name}</strong>
                  <small>
                    {item.provider_key === "github"
                      ? t(
                          item.provider_config_version ===
                            "github_notifications_v1"
                            ? "GitHub account · Polling"
                            : "GitHub App · Webhook",
                        )
                      : item.provider_key === "slack"
                        ? "Slack"
                        : t("Feishu")}
                    {typeof organization === "string" &&
                      organization &&
                      ` · ${organization}`}
                  </small>
                </span>
                <CaretRightIcon aria-hidden="true" />
              </Link>
            </li>
          );
        })}
      </ul>
      {accounts.items.length === 0 && (
        <>
          <p>
            {t(
              "No accounts for this platform yet. Create one to connect your bot.",
            )}
          </p>
          <Button variant="outline" onClick={onCreate}>
            {t("Create a new account")}
          </Button>
        </>
      )}
      {accounts.next_cursor && (
        <Link to={`${basePath}/bots`}>{t("View all bots")}</Link>
      )}
    </section>
  );
}

export function CallbackSetup({ account }: { account: Schema["Account"] }) {
  return account.provider_config.event_transport === "websocket" ? (
    <EventConnection account={account} />
  ) : (
    <HttpCallbackSetup account={account} />
  );
}

function HttpCallbackSetup({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["bot-setup", account.workspace_id, account.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/setup", {
          params: { path: { account_id: account.id } },
          signal,
        })
        .then(data),
  });
  if (account.provider_config_version === "github_notifications_v1")
    return (
      <section>
        <h2>{t("Notification polling")}</h2>
        <p>
          {t(
            "Only outbound GitHub access is required. Mention this account or subscribe it to an Issue or PR in a configured repository. Polling starts when reception is enabled.",
          )}
        </p>
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.data?.poll_checked_at && (
          <p>
            {t("Last checked")}: {query.data.poll_checked_at}
          </p>
        )}
        {query.data?.poll_error_code && (
          <p role="status">
            {t("Polling failed")}: {query.data.poll_error_code}
          </p>
        )}
      </section>
    );
  return (
    <section>
      <h2>{t("Configure HTTP events")}</h2>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <>
          <p>
            {t(
              "Set this event endpoint in your provider's app settings. Provider URL verification works while reception is disabled.",
            )}
          </p>
          <div className={styles.endpoint}>
            <code>{query.data.event_url ?? query.data.event_path ?? ""}</code>
            <CopyButton
              value={query.data.event_url ?? query.data.event_path ?? ""}
              copyLabel={t("Copy event endpoint")}
            />
          </div>
          {!query.data.event_url && (
            <p role="status">
              {t(
                "No public event origin is configured. Ask the deployment administrator to configure it before connecting the provider.",
              )}
            </p>
          )}
          <p>
            {t(
              "The provider must be able to reach this endpoint. A localhost address is only usable for local tests.",
            )}
          </p>
        </>
      )}
    </section>
  );
}
