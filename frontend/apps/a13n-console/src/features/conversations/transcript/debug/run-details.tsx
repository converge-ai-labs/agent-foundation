import { DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../../auth/context";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../../../shared/feedback";
import { CopyableId } from "../../../../shared/identity";
import { JsonView } from "../../../../shared/forms";
import { UNKNOWN } from "../../../../shared/unknown";
import { AgentLink } from "../../../agents/link";
import { useAgent } from "../../../agents/queries";
import { EnvironmentReference } from "../../../environments/reference";
import { conversationQueries, runPath } from "../../api";
import { RunEnvironmentMounts } from "../../environment-mounts";
import { useRun } from "../../queries";
import { formatDuration } from "../../format";
import { RunEvents } from "./run-events";
import styles from "./details.module.css";

/**
 * Everything the heading does not say: identifiers, configuration evidence,
 * attempts, lifecycle events and the trace of this exact Run.
 */
export function RunDetails({ runId }: { runId: string }) {
  const { t } = useTranslation(),
    { workspace, basePath, can } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useRun(runId);
  const attempts = useQuery(queries.attempts(runId));
  const lineage = useQuery(queries.lineage(runId));
  const run = runQuery.data;
  const agent = useAgent(run?.agent_revision_id ? run.agent_id : undefined);
  if (runQuery.isPending)
    return (
      <div className={styles.details}>
        <Loading variant="detail" />
      </div>
    );
  if (!run)
    return (
      <div className={styles.details}>
        <ErrorNotice
          error={runQuery.error}
          retry={() => void runQuery.refetch()}
        />
      </div>
    );
  const duration =
    run.started_at && run.completed_at
      ? Date.parse(run.completed_at) - Date.parse(run.started_at)
      : null;
  return (
    <div className={styles.details}>
      <div className={styles.detailColumns}>
        <Facts
          label={t("Overview")}
          rows={[
            [t("Run"), <CopyableId key="run" value={run.id} />],
            [t("Thread"), <CopyableId key="thread" value={run.thread_id} />],
            [
              t("Trigger source"),
              t(`trigger.${run.trigger_type}`, {
                defaultValue: run.trigger_type,
              }),
            ],
            [
              t("Started"),
              run.started_at ? <Timestamp value={run.started_at} /> : UNKNOWN,
            ],
            [
              t("Completed"),
              run.completed_at ? (
                <Timestamp value={run.completed_at} />
              ) : (
                UNKNOWN
              ),
            ],
            [t("Duration"), formatDuration(duration)],
          ]}
        />
        <Facts
          label={t("Configuration")}
          rows={[
            [
              t("Agent"),
              run.agent_revision_id ? (
                <AgentLink key="agent" agentId={run.agent_id}>
                  {agent.data?.name ?? run.agent_id}
                </AgentLink>
              ) : (
                t("Configuration assistant")
              ),
            ],
            [
              t("Agent revision"),
              run.agent_revision_id ? (
                <CopyableId key="revision" value={run.agent_revision_id} />
              ) : (
                t("None")
              ),
            ],
            [
              t("Effective configuration digest"),
              <CopyableId
                key="digest"
                value={run.effective_agent_config_digest}
              />,
            ],
            ...(can("trace.read")
              ? [
                  [
                    t("Traces"),
                    <Link
                      key="traces"
                      to={`${basePath}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.completed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
                    >
                      {t("Open run traces")}
                    </Link>,
                  ] as [string, ReactNode],
                ]
              : []),
          ]}
        />
      </div>
      <div className={styles.detailBlock}>
        <span className={styles.detailLabel}>{t("Environment")}</span>
        {run.environment_id ? (
          <EnvironmentReference id={run.environment_id} />
        ) : (
          <p className={styles.detailNote}>{t("None")}</p>
        )}
        {run.environment_working_directory && (
          <code className={styles.detailPath}>
            {run.environment_working_directory}
          </code>
        )}
      </div>
      <div className={styles.detailColumns}>
        <div className={styles.detailBlock}>
          <span className={styles.detailLabel}>{t("Attempts")}</span>
          <ErrorNotice error={attempts.error} />
          {attempts.isPending ? (
            <Loading variant="list" rows={2} />
          ) : !attempts.data?.length ? (
            <p className={styles.detailNote}>{t("No attempts recorded.")}</p>
          ) : (
            <ol className={styles.attempts}>
              {attempts.data.map((attempt) => (
                <Attempt key={attempt.id} attempt={attempt} />
              ))}
            </ol>
          )}
        </div>
        <RunEvents runId={runId} />
      </div>
      <div className={styles.detailBlock}>
        <span className={styles.detailLabel}>{t("Lineage")}</span>
        <ErrorNotice error={lineage.error} />
        {lineage.data &&
          (!lineage.data.items.length ? (
            <p className={styles.detailNote}>{t("No related runs.")}</p>
          ) : (
            <ol className={styles.lineageList}>
              {lineage.data.items.map((entry) => (
                <li key={entry.run_id}>
                  <Link to={runPath(basePath, entry, "debug")}>
                    {t(
                      entry.lineage_kind === "fork"
                        ? "Branch run"
                        : entry.lineage_kind === "continue"
                          ? "Continued run"
                          : "Initial run",
                    )}
                  </Link>
                  <StatePill state={entry.status} />
                  <Timestamp value={entry.created_at} relative />
                </li>
              ))}
            </ol>
          ))}
      </div>
      <RunEnvironmentMounts key={run.id} run={run} />
      <DisclosureSection
        className={styles.detailDisclosure}
        title={<>{t("Raw metadata")}</>}
      >
        <JsonView value={run} />
      </DisclosureSection>
    </div>
  );
}

function Facts({
  label,
  rows,
}: {
  label: string;
  rows: [string, ReactNode][];
}) {
  return (
    <div className={styles.detailBlock}>
      <span className={styles.detailLabel}>{label}</span>
      <dl className={styles.facts}>
        {rows.map(([term, value], index) => (
          <div key={index}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Attempt({ attempt }: { attempt: Schema["RunAttemptResource"] }) {
  const { t } = useTranslation();
  return (
    <li className={styles.attempt}>
      <span className={styles.attemptHead}>
        <strong>
          {t("Attempt {{attempt}}", { attempt: attempt.attempt_number })}
        </strong>
        <StatePill state={attempt.status} />
      </span>
      <span className={styles.attemptSpan}>
        <Timestamp value={attempt.started_at} />
        {attempt.finished_at && (
          <>
            {" → "}
            <Timestamp value={attempt.finished_at} />
          </>
        )}
      </span>
      {(attempt.start_reason || attempt.yield_reason) && (
        <span className={styles.detailNote}>
          {[attempt.start_reason, attempt.yield_reason]
            .filter(Boolean)
            .join(" · ")}
        </span>
      )}
      {attempt.failure != null && (
        <DisclosureSection
          className={styles.detailDisclosure}
          title={<>{t("Error details")}</>}
        >
          <JsonView value={attempt.failure} />
        </DisclosureSection>
      )}
    </li>
  );
}
