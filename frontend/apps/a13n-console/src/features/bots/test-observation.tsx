import {
  ArrowRightIcon,
  ArrowClockwiseIcon,
  CheckIcon,
  ClockIcon,
  XIcon,
} from "@phosphor-icons/react";
import { Button, DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { runPath } from "../conversations/api";
import { ReplyPill } from "../integrations/platform";
import styles from "./test-observation.module.css";

type Stage = "confirmed" | "failed" | "waiting";

function StageMark({ state }: { state: Stage }) {
  return (
    <span className={styles.stageMark} data-state={state} aria-hidden="true">
      {state === "confirmed" ? (
        <CheckIcon size={12} weight="bold" />
      ) : state === "failed" ? (
        <XIcon size={12} weight="bold" />
      ) : (
        <ClockIcon size={12} />
      )}
    </span>
  );
}

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
  const received: Stage = test.event_received_at ? "confirmed" : "waiting";
  const accepted: Stage = test.accepted_at
    ? "confirmed"
    : test.rejection_code
      ? "failed"
      : "waiting";
  const replied: Stage =
    test.reply?.status === "succeeded"
      ? "confirmed"
      : test.reply?.status === "rejected"
        ? "failed"
        : "waiting";
  return (
    <div className={styles.observation} aria-label={t("Test observations")}>
      {stale && (
        <p className={styles.notice} data-tone="warning" role="status">
          {t(
            "Configuration changed. These observations belong to the previous configuration; prepare a new test.",
          )}
        </p>
      )}
      {showMessage && !stale && !expired && !test.event_received_at && (
        <div className={styles.testMessage}>
          <div className={styles.testMessageRow}>
            <code>{message}</code>
            <CopyButton value={message} copyLabel={t("Copy test message")} />
          </div>
          <p>
            {t(
              "Replace @bot with a real mention of your bot. Send this once before the deadline.",
            )}
          </p>
          <p>
            {t("Send before")}: <Timestamp value={test.expires_at} />
          </p>
        </div>
      )}
      {expired && (
        <p className={styles.notice} data-tone="danger" role="status">
          {t(
            "No matching message arrived before the deadline. Prepare a new test message.",
          )}
        </p>
      )}
      <ol className={styles.stages}>
        <li>
          <StageMark state={received} />
          <div className={styles.stageCopy}>
            <strong>
              {test.event_received_at
                ? t("Test message received")
                : t("Awaiting test message")}
            </strong>
            <small>
              {test.event_received_at ? (
                <Timestamp value={test.event_received_at} />
              ) : (
                t("Not yet observed")
              )}
            </small>
          </div>
        </li>
        <li>
          <StageMark state={accepted} />
          <div className={styles.stageCopy}>
            <strong>
              {test.accepted_at
                ? t("Agent execution accepted")
                : test.rejection_code
                  ? t("Test message was not accepted for execution")
                  : t("Awaiting execution acceptance")}
            </strong>
            <small>
              {test.accepted_at ? (
                <Timestamp value={test.accepted_at} />
              ) : (
                t("Not yet observed")
              )}
            </small>
            {test.steer_id && (
              <p>{t("Delivered to an existing run as a follow-up message.")}</p>
            )}
            {test.rejection_code && (
              <p>
                <code>{test.rejection_code}</code>
                {" · "}
                {t(
                  "Review the conversation response policy and execution permissions before testing again.",
                )}
              </p>
            )}
          </div>
          {test.run_id && test.session_id && test.thread_id && (
            <Button
              size="sm"
              variant="ghost"
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
        </li>
        <li>
          <StageMark state={replied} />
          <div className={styles.stageCopy}>
            <strong>{t("Platform reply")}</strong>
            <small>
              {test.reply ? (
                <Timestamp
                  value={test.reply.finished_at ?? test.reply.started_at}
                />
              ) : (
                t("Not yet observed")
              )}
            </small>
            {test.reply?.status === "rejected" && (
              <p>
                {t(
                  "Review the provider permissions and conversation access before testing again.",
                )}
              </p>
            )}
            {test.reply?.status === "outcome_unknown" && (
              <p>
                {t(
                  "The platform may have received the reply. Check the conversation before sending another test.",
                )}
              </p>
            )}
            {!test.reply && (
              <p>
                {t(
                  "No provider-confirmed reply containing this test marker has been observed.",
                )}
              </p>
            )}
          </div>
          {test.reply && <ReplyPill status={test.reply.status} />}
        </li>
      </ol>
      {showRefresh && (
        <div>
          <Button variant="outline" disabled={refreshing} onClick={refresh}>
            <ArrowClockwiseIcon aria-hidden="true" />
            {t("Refresh observations")}
          </Button>
        </div>
      )}
      <DisclosureSection title={<>{t("Test details")}</>}>
        <p className={styles.detail}>
          {t("Pilot conversation")}: <code>{test.external_target_id}</code>
        </p>
        <p className={styles.detail}>
          {t(
            "Refreshing only reads observations; it does not send messages or rerun the agent.",
          )}
        </p>
      </DisclosureSection>
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
    <Section
      title={t("Latest setup test")}
      description={t(
        "What the last guided test observed: message reception, agent execution, and the platform reply.",
      )}
      actions={
        <>
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
          </Button>
        </>
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="list" rows={3} />
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
          <p className={styles.detail}>{t("No setup test recorded")}</p>
        ))
      )}
    </Section>
  );
}
