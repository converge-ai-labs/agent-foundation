import { ArrowSquareOutIcon, CaretRightIcon } from "@phosphor-icons/react";
import { Button, Tabs, TabsList, TabsTab } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { CatalogTile, CatalogTiles } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { Page } from "../../shared/page";
import { AccountForm } from "../application-accounts/form";
import { PlatformIcon, usePlatformName } from "../integrations/platform";
import { BotChecks } from "./checks";
import { CallbackSetup } from "./event-setup";
import { BotPilot } from "./pilot";
import { PilotTest } from "./pilot-test";
import { BotSetupInstructions } from "./setup-instructions";
import { Step, StepRail } from "./wizard";
import styles from "./connect.module.css";

export { CallbackSetup } from "./event-setup";

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
    { t } = useTranslation(),
    platformName = usePlatformName();
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
  function resetPlatform() {
    setPlatform(null);
    setAccountMode(null);
    setGithubMode("github_polling");
  }
  return (
    <Page
      title={t("Connect a bot")}
      back={`${basePath}/bots`}
      backLabel={t("Bots")}
      description={t(
        "Connect a platform identity you own, then choose an agent and reception scope.",
      )}
    >
      <div className={styles.layout}>
        <StepRail steps={steps} current={step} label={t("Bot setup steps")} />
        <div className={styles.content}>
          {!accountId && !platform && (
            <Step
              title={t("Choose your platform")}
              description={t("Where will your bot respond?")}
            >
              <CatalogTiles>
                {(["slack", "lark", "github"] as const).map((value) => (
                  <CatalogTile
                    key={value}
                    icon={<PlatformIcon type={value} size={22} />}
                    name={platformName(value)}
                    detail={t(
                      value === "github"
                        ? "Issues and pull requests"
                        : "Channels and group chats",
                    )}
                    onClick={() => setPlatform(value)}
                  />
                ))}
              </CatalogTiles>
            </Step>
          )}
          {!accountId && platform && (
            <Step
              title={t("Choose an account")}
              description={t(
                "Use a platform identity you already connected, or create a new one.",
              )}
              onBack={resetPlatform}
              backLabel={t("Change platform")}
            >
              <div className={styles.selectedPlatform}>
                <IconTile size={32} tone="elevated">
                  <PlatformIcon type={platform} size={18} />
                </IconTile>
                <strong>{platformName(platform)}</strong>
              </div>
              <ErrorNotice
                error={accounts.error}
                retry={() => void accounts.refetch()}
              />
              {accounts.isPending && <Loading variant="list" rows={3} />}
              {accounts.isSuccess && (
                <>
                  <Tabs
                    value={mode}
                    onValueChange={(value) => setAccountMode(String(value))}
                  >
                    <TabsList aria-label={t("Account source")}>
                      <TabsTab value="existing">
                        {t("Use an existing account")}
                      </TabsTab>
                      <TabsTab value="new">{t("Create a new account")}</TabsTab>
                    </TabsList>
                  </Tabs>
                  {mode === "existing" ? (
                    <ExistingAccounts
                      accounts={accounts.data}
                      onCreate={() => setAccountMode("new")}
                    />
                  ) : (
                    <div className={styles.newAccount}>
                      {platform === "github" && (
                        <fieldset className={styles.githubModes}>
                          <legend>{t("GitHub connection type")}</legend>
                          {(
                            [
                              [
                                "github_polling",
                                "GitHub account · Polling",
                                "Use an account token. No public callback needed.",
                              ],
                              [
                                "github",
                                "GitHub App · Webhook",
                                "Use an installed App and a public webhook address.",
                              ],
                            ] as const
                          ).map(([value, label, hint]) => (
                            <label
                              key={value}
                              data-checked={githubMode === value}
                            >
                              <input
                                type="radio"
                                name="github-mode"
                                value={value}
                                checked={githubMode === value}
                                onChange={() => setGithubMode(value)}
                              />
                              <span>
                                <strong>{t(label)}</strong>
                                <small>{t(hint)}</small>
                              </span>
                            </label>
                          ))}
                        </fieldset>
                      )}
                      {platform === "github" ? (
                        <div className={styles.instructions}>
                          <p>
                            {t(
                              setupProvider === "github_polling"
                                ? "Use a dedicated GitHub account and a classic PAT: notifications plus public_repo for public repositories, or repo for private repositories. No public callback address is needed."
                                : "Create and install a GitHub App with Issues and Pull requests read/write permissions. Subscribe to Issues, Issue comments, Pull requests, Pull request reviews, and Pull request review comments. A public webhook address is required.",
                            )}
                          </p>
                          <Button
                            variant="outline"
                            size="sm"
                            render={
                              <a
                                href={
                                  setupProvider === "github_polling"
                                    ? "https://github.com/settings/tokens"
                                    : "https://github.com/settings/apps"
                                }
                                target="_blank"
                                rel="noreferrer"
                              />
                            }
                          >
                            {t("Open GitHub settings")}
                            <ArrowSquareOutIcon size={13} aria-hidden="true" />
                          </Button>
                        </div>
                      ) : (
                        <BotSetupInstructions platform={platform} />
                      )}
                      <p className={styles.hint}>
                        {t(
                          "The account is saved with reception disabled. Only explicitly configured conversations will be admitted when you enable it.",
                        )}{" "}
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
                    </div>
                  )}
                </>
              )}
            </Step>
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
                {step === 2 && (
                  <Step
                    title={t("Verify the installation")}
                    description={t(
                      "Confirm that the verified external organization is the installation you intend to connect.",
                    )}
                    onBack={() => setSearch({})}
                    backLabel={t("Change account")}
                    primary={
                      <Button
                        disabled={!verified || check.isFetching}
                        onClick={() => advance("reception")}
                      >
                        {t("Continue")}
                      </Button>
                    }
                  >
                    <div className={styles.account}>
                      <IconTile size={32} tone="elevated">
                        <PlatformIcon type={current.provider_key} size={18} />
                      </IconTile>
                      <span>
                        <strong>{current.name}</strong>
                        <small>{platformName(current.provider_key)}</small>
                      </span>
                      <Link
                        to={`${basePath}/application-accounts/${current.id}`}
                      >
                        {t("Review account identity and credentials")}
                      </Link>
                    </div>
                    <CallbackSetup account={current} />
                    <BotChecks account={current} />
                  </Step>
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
    { t } = useTranslation(),
    platformName = usePlatformName();
  if (!accounts.items.length)
    return (
      <div className={styles.instructions}>
        <p>
          {t(
            "No accounts for this platform yet. Create one to connect your bot.",
          )}
        </p>
        <Button variant="outline" size="sm" onClick={onCreate}>
          {t("Create a new account")}
        </Button>
      </div>
    );
  return (
    <div className={styles.existing}>
      {accounts.items.map(({ account: item }) => {
        const organization =
          item.provider_config?.[
            item.provider_key === "slack" ? "team_id" : "tenant_key"
          ];
        const source =
          item.provider_key === "github"
            ? t(
                item.provider_config_version === "github_notifications_v1"
                  ? "GitHub account · Polling"
                  : "GitHub App · Webhook",
              )
            : platformName(item.provider_key);
        return (
          <Link key={item.id} to={`${basePath}/bots/${item.id}`}>
            <IconTile size={32} tone="elevated">
              <PlatformIcon type={item.provider_key} size={18} />
            </IconTile>
            <span className={styles.existingCopy}>
              <strong>{item.name}</strong>
              <small>
                {typeof organization === "string" && organization
                  ? `${source} · ${organization}`
                  : source}
              </small>
            </span>
            <CaretRightIcon aria-hidden="true" size={13} />
          </Link>
        );
      })}
      {accounts.next_cursor && (
        <Link className={styles.moreLink} to={`${basePath}/bots`}>
          {t("View all bots")}
        </Link>
      )}
    </div>
  );
}
