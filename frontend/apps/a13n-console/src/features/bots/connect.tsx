import { Button } from "a13n-ui";
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
  const [platform, setPlatform] = useState<"slack" | "lark" | null>(null);
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
      ["slack", "lark"].includes(current.provider_key),
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
    ? platform
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
        "Connect an app you own. Each installation uses one application account.",
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
            <>
              <h2>{t("Choose your platform")}</h2>
              <p>
                {t(
                  "Use one account for each Slack workspace or Feishu installation.",
                )}
              </p>
              <div className={styles.platforms}>
                <Button variant="outline" onClick={() => setPlatform("slack")}>
                  Slack
                </Button>
                <Button variant="outline" onClick={() => setPlatform("lark")}>
                  {t("Feishu")}
                </Button>
              </div>
              <ExistingAccounts />
            </>
          )}
          {!accountId && platform && (
            <>
              <BotSetupInstructions platform={platform} />
              <p>
                {t(
                  "The account is saved with reception disabled. Only explicitly configured conversations will be admitted when you enable it.",
                )}
              </p>
              <p>
                {t(
                  "Enter the installation identifiers from your app settings. They are verified in the next step; entering an ID does not prove access.",
                )}
              </p>
              <AccountForm
                key={platform}
                bot
                setupProvider={platform}
                onCancel={() => setPlatform(null)}
                onSuccess={saved}
              />
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
            !["slack", "lark"].includes(current.provider_key)) ? (
            <Empty
              title={t("Bot not found")}
              description={t("Choose a Slack or Feishu bot in this workspace.")}
            />
          ) : (
            current && (
              <>
                <p className={styles.account}>
                  {current.name} ·{" "}
                  {current.provider_key === "slack" ? "Slack" : t("Feishu")}
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

function ExistingAccounts() {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [show, setShow] = useState(false);
  const query = useQuery({
    queryKey: ["bot-setup-existing", workspace.id],
    enabled: show,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/application-accounts", {
          params: {
            path: { workspace: workspace.id },
            query: { bots_only: true, limit: 20 },
          },
          signal,
        })
        .then(data),
  });
  return (
    <section>
      <Button
        variant="ghost"
        onClick={() => setShow(!show)}
        aria-expanded={show}
      >
        {t("Use an existing account")}
      </Button>
      {show && (
        <>
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
          {query.isPending ? (
            <Loading />
          ) : (
            <ul className={styles.existing}>
              {query.data?.items.map((item) => (
                <li key={item.id}>
                  <Link to={`${basePath}/bots/${item.id}`}>{item.name}</Link>
                  <small>
                    {item.provider_key === "slack" ? "Slack" : t("Feishu")}
                  </small>
                </li>
              ))}
            </ul>
          )}
          {query.data?.items.length === 0 && <p>{t("No bots connected")}</p>}
          {query.data?.next_cursor && (
            <Link to={`${basePath}/bots`}>{t("View all bots")}</Link>
          )}
        </>
      )}
    </section>
  );
}

function CallbackSetup({ account }: { account: Schema["Account"] }) {
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
            <code>{query.data.event_url ?? query.data.event_path}</code>
            <CopyButton
              value={query.data.event_url ?? query.data.event_path}
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
