import {
  ArrowRightIcon,
  ArrowClockwiseIcon,
  CheckIcon,
  ClockIcon,
  XIcon,
} from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { runPath } from "../conversations/api";
import styles from "./connect.module.css";
import summaryStyles from "./test-observation.module.css";

export function TestObservation({
  account,
  test,
  refresh,
  refreshing,
  showMessage = false,
  showRefresh = true,
}: {
  account: Schema["Account"];
  test: Schema["BotTest"];
  refresh: () => void;
  refreshing: boolean;
  showMessage?: boolean;
  showRefresh?: boolean;
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
    <div
      className={summaryStyles.observation}
      aria-label={t("Test observations")}
    >
      {stale && (
        <p className={summaryStyles.notice} role="status">
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
        <p className={summaryStyles.notice} role="status">
          {t(
            "No matching message arrived before the deadline. Prepare a new test message.",
          )}
        </p>
      )}
      <ol className={summaryStyles.steps}>
        <li>
          <span
            className={summaryStyles.stepIcon}
            data-state={test.event_received_at ? "confirmed" : "waiting"}
            aria-hidden="true"
          >
            {test.event_received_at ? <CheckIcon /> : <ClockIcon />}
          </span>
          <strong>
            {test.event_received_at
              ? t("Test message received")
              : t("Awaiting test message")}
          </strong>
          <div className={summaryStyles.stepTime}>
            {test.event_received_at ? (
              <Timestamp value={test.event_received_at} />
            ) : (
              t("Not yet observed")
            )}
          </div>
          <span />
        </li>
        <li>
          <span
            className={summaryStyles.stepIcon}
            data-state={
              test.accepted_at
                ? "confirmed"
                : test.rejection_code
                  ? "failed"
                  : "waiting"
            }
            aria-hidden="true"
          >
            {test.accepted_at ? (
              <CheckIcon />
            ) : test.rejection_code ? (
              <XIcon />
            ) : (
              <ClockIcon />
            )}
          </span>
          <strong>
            {test.accepted_at
              ? t("Agent execution accepted")
              : test.rejection_code
                ? t("Test message was not accepted for execution")
                : t("Awaiting execution acceptance")}
          </strong>
          <div className={summaryStyles.stepTime}>
            {test.accepted_at ? (
              <Timestamp value={test.accepted_at} />
            ) : (
              t("Not yet observed")
            )}
          </div>
          <div className={summaryStyles.stepAction}>
            {test.run_id && test.session_id && test.thread_id && (
              <Button
                size="sm"
                variant="outline"
                render={
                  <Link
                    to={runPath(basePath, {
                      run_id: test.run_id,
                      session_id: test.session_id,
                      thread_id: test.thread_id,
                    })}
                  />
                }
              >
                {t("Open run")}
                <ArrowRightIcon aria-hidden="true" />
              </Button>
            )}
          </div>
          {test.steer_id && (
            <p className={summaryStyles.stepNote}>
              {t("Delivered to an existing run as a follow-up message.")}
            </p>
          )}
          {test.rejection_code && (
            <p className={summaryStyles.stepNote}>
              <code>{test.rejection_code}</code>
              {" · "}
              {t(
                "Review the conversation response policy and execution permissions before testing again.",
              )}
            </p>
          )}
        </li>
        <li>
          <span
            className={summaryStyles.stepIcon}
            data-state={
              test.reply?.status === "succeeded"
                ? "confirmed"
                : test.reply?.status === "rejected"
                  ? "failed"
                  : "waiting"
            }
            aria-hidden="true"
          >
            {test.reply?.status === "succeeded" ? (
              <CheckIcon />
            ) : test.reply?.status === "rejected" ? (
              <XIcon />
            ) : (
              <ClockIcon />
            )}
          </span>
          <div className={summaryStyles.stepLabel}>
            <strong>{t("Platform reply")}</strong>
            {test.reply && (
              <StatePill
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
            )}
          </div>
          <div className={summaryStyles.stepTime}>
            {test.reply ? (
              <Timestamp
                value={test.reply.finished_at ?? test.reply.started_at}
              />
            ) : (
              t("Not yet observed")
            )}
          </div>
          <span />
          {test.reply?.status === "rejected" && (
            <p className={summaryStyles.stepNote}>
              {t(
                "Review the provider permissions and conversation access before testing again.",
              )}
            </p>
          )}
          {test.reply?.status === "outcome_unknown" && (
            <p className={summaryStyles.stepNote}>
              {t(
                "The platform may have received the reply. Check the conversation before sending another test.",
              )}
            </p>
          )}
          {!test.reply && (
            <p className={summaryStyles.stepNote}>
              {t(
                "No provider-confirmed reply containing this test marker has been observed.",
              )}
            </p>
          )}
        </li>
      </ol>
      {showRefresh && (
        <Button variant="outline" disabled={refreshing} onClick={refresh}>
          <ArrowClockwiseIcon aria-hidden="true" />
          {t("Refresh observations")}
        </Button>
      )}
      <details className={summaryStyles.details}>
        <summary>{t("Test details")}</summary>
        <p>
          {t("Pilot conversation")}: <code>{test.external_target_id}</code>
        </p>
        <p>
          {t(
            "Refreshing only reads observations; it does not send messages or rerun the agent.",
          )}
        </p>
      </details>
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
      <header className={summaryStyles.header}>
        <h2>{t("Latest setup test")}</h2>
        <div className={summaryStyles.actions}>
          <Button
            size="sm"
            variant="ghost"
            aria-label={t("Refresh observations")}
            title={t("Refresh observations")}
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <ArrowClockwiseIcon aria-hidden="true" />
            {t("Refresh")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            render={
              <Link
                to={`${basePath}/bots/connect?account=${account.id}&step=test`}
              />
            }
          >
            {t("Review test setup")}
            <ArrowRightIcon aria-hidden="true" />
          </Button>
        </div>
      </header>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        !query.error &&
        (query.data?.latest ? (
          <TestObservation
            account={account}
            test={query.data.latest}
            showRefresh={false}
            refreshing={query.isFetching}
            refresh={() => void query.refetch()}
          />
        ) : (
          <p>{t("No setup test recorded")}</p>
        ))
      )}
    </section>
  );
}
