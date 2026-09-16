import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { CopyButton } from "../../shared/copy";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { runPath } from "../conversations/api";
import styles from "./connect.module.css";

export function TestObservation({
  account,
  test,
  refresh,
  refreshing,
  showMessage = false,
}: {
  account: Schema["Account"];
  test: Schema["BotTest"];
  refresh: () => void;
  refreshing: boolean;
  showMessage?: boolean;
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace();
  const expired =
    !!test &&
    !test.event_received_at &&
    Date.parse(test.expires_at) <= Date.now();
  const stale =
    !!test &&
    (test.stale ||
      test.account_version !== account.version ||
      test.credential_generation !== account.credential_generation);
  const message = test
    ? t("@bot Please reply with exactly this test marker: {{marker}}", {
        marker: test.id,
      })
    : "";
  return (
    <div aria-label={t("Test observations")}>
      <p>
        {t("Pilot conversation")}: <code>{test.external_target_id}</code>
      </p>
      {stale && (
        <p role="status">
          {t(
            "Configuration changed. These observations belong to the previous configuration; prepare a new test.",
          )}
        </p>
      )}
      {showMessage && !stale && !expired && !test.event_received_at && (
        <>
          <blockquote className={styles.testMessage}>{message}</blockquote>
          <CopyButton value={message} copyLabel={t("Copy test message")} />
          <p>
            {t(
              "Replace @bot with a real mention of your bot. Send this once before the deadline.",
            )}
          </p>
          <p>
            {t("Send before")}: <Timestamp value={test.expires_at} />
          </p>
        </>
      )}
      {expired && (
        <p role="status">
          {t(
            "No matching message arrived before the deadline. Prepare a new test message.",
          )}
        </p>
      )}
      <ol className={styles.testStages}>
        <li>
          <strong>
            {test.event_received_at
              ? t("Test message received")
              : t("Awaiting test message")}
          </strong>
          {test.event_received_at && (
            <Timestamp value={test.event_received_at} />
          )}
        </li>
        <li>
          <strong>
            {test.accepted_at
              ? t("Agent execution accepted")
              : test.rejection_code
                ? t("Test message was not accepted for execution")
                : t("Awaiting execution acceptance")}
          </strong>
          {test.accepted_at && <Timestamp value={test.accepted_at} />}
          {test.steer_id && (
            <p>{t("Delivered to an existing run as a follow-up message.")}</p>
          )}
          {test.rejection_code && (
            <>
              <code>{test.rejection_code}</code>
              <p>
                {t(
                  "Review the conversation response policy and execution permissions before testing again.",
                )}
              </p>
            </>
          )}
          {test.run_id && test.session_id && test.thread_id && (
            <Link
              to={runPath(basePath, {
                run_id: test.run_id,
                session_id: test.session_id,
                thread_id: test.thread_id,
              })}
            >
              {t("Open run")}
            </Link>
          )}
        </li>
        <li>
          <strong>{t("Platform reply")}</strong>
          {test.reply ? (
            <>
              <StateBadge
                state={test.reply.status}
                label={t(
                  (
                    {
                      succeeded: "Provider confirmed test reply",
                      dispatching: "Awaiting reply confirmation",
                      rejected: "Reply rejected",
                      outcome_unknown: "Reply outcome unknown",
                    } as const
                  )[test.reply.status],
                )}
              />
              <Timestamp
                value={test.reply.finished_at ?? test.reply.started_at}
              />
              {test.reply.status === "rejected" && (
                <p>
                  {t(
                    "Review the provider permissions and conversation access before testing again.",
                  )}
                </p>
              )}
              {test.reply.status === "outcome_unknown" && (
                <p>
                  {t(
                    "The platform may have received the reply. Check the conversation before sending another test.",
                  )}
                </p>
              )}
            </>
          ) : (
            <p>
              {t(
                "No provider-confirmed reply containing this test marker has been observed.",
              )}
            </p>
          )}
        </li>
      </ol>
      <Button variant="outline" disabled={refreshing} onClick={refresh}>
        {t("Refresh observations")}
      </Button>
      <p>
        {t(
          "Refreshing only reads observations; it does not send messages or rerun the agent.",
        )}
      </p>
    </div>
  );
}

export function LatestBotTest({ account }: { account: Schema["Account"] }) {
  const { can } = useWorkspace();
  return can("application_account.manage") ? (
    <AdminTest account={account} />
  ) : null;
}

function AdminTest({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["bot-setup-test", workspace.id, account.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/tests/latest", {
          params: { path: { account_id: account.id } },
          signal,
        })
        .then(data),
  });
  return (
    <section aria-label={t("Latest setup test")}>
      <h2>{t("Latest setup test")}</h2>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        !query.error &&
        (query.data?.latest ? (
          <TestObservation
            account={account}
            test={query.data.latest}
            refreshing={query.isFetching}
            refresh={() => void query.refetch()}
          />
        ) : (
          <p>{t("No setup test recorded")}</p>
        ))
      )}
      <Link to={`${basePath}/bots/connect?account=${account.id}&step=test`}>
        {t("Review test setup")}
      </Link>
    </section>
  );
}
