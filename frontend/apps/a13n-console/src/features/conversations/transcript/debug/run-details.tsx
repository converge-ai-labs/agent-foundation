import { Button, DisclosureSection } from "a13n-ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { ThreadMemoryMounts } from "../../memory-mounts";
import { MemoryMountRows } from "../../../memories/mounts";
import { useRun } from "../../queries";
import { formatDuration } from "../../format";
import styles from "./details.module.css";

/**
 * Everything the heading does not say: identifiers, configuration evidence,
 * attempts and the trace of this exact Run.
 */
export function RunDetails({ runId }: { runId: string }) {
  const { t } = useTranslation(),
    { workspace, basePath, can } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useRun(runId);
  const attempts = useQuery(queries.attempts(runId));
  const lineage = useInfiniteQuery(queries.lineage(runId));
  const related = lineage.data?.pages.flatMap((page) => page.items);
  const run = runQuery.data;
  const agent = useAgent(run?.agent_id);
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
    run.started_at && run.sealed_at
      ? Date.parse(run.sealed_at) - Date.parse(run.started_at)
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
              t(`trigger.${run.trigger}`, { defaultValue: run.trigger }),
            ],
            [
              t("Started"),
              run.started_at ? <Timestamp value={run.started_at} /> : UNKNOWN,
            ],
            [
              t("Completed"),
              run.sealed_at ? <Timestamp value={run.sealed_at} /> : UNKNOWN,
            ],
            [t("Duration"), formatDuration(duration)],
          ]}
        />
        <Facts
          label={t("Configuration")}
          rows={[
            [
              t("Agent"),
              <AgentLink key="agent" agentId={run.agent_id}>
                {agent.data?.name ?? run.agent_id}
              </AgentLink>,
            ],
            [
              t("Agent revision"),
              <CopyableId key="revision" value={run.agent_revision_id} />,
            ],
            ...(can("read")
              ? [
                  [
                    t("Traces"),
                    <Link
                      key="traces"
                      to={`${basePath}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.sealed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
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
        {!run.environment_mounts.length ? (
          <p className={styles.detailNote}>{t("None")}</p>
        ) : (
          run.environment_mounts.map((mount) => (
            <div key={mount.name}>
              <strong>{mount.name}</strong>
              <EnvironmentReference id={mount.environment_id} />
              {mount.working_directory && (
                <code className={styles.detailPath}>
                  {mount.working_directory}
                </code>
              )}
            </div>
          ))
        )}
      </div>
      <div className={styles.detailBlock}>
        <span className={styles.detailLabel}>{t("Memory")}</span>
        {!run.memory_mounts.length ? (
          <p className={styles.detailNote}>{t("None")}</p>
        ) : (
          <MemoryMountRows
            mounts={run.memory_mounts}
            empty={t("None")}
            runId={run.id}
          />
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
      </div>
      <div className={styles.detailBlock}>
        <span className={styles.detailLabel}>{t("Lineage")}</span>
        <ErrorNotice error={lineage.error} />
        {related &&
          (!related.length ? (
            <p className={styles.detailNote}>{t("No related runs.")}</p>
          ) : (
            <ol className={styles.lineageList}>
              {related.map((entry) => (
                <li key={entry.id}>
                  <Link
                    to={runPath(
                      basePath,
                      { ...entry, run_id: entry.id },
                      "debug",
                    )}
                  >
                    {t(
                      entry.lineage === "fork"
                        ? "Branch run"
                        : entry.lineage === "continue"
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
        {lineage.hasNextPage && (
          <Button
            size="sm"
            variant="ghost"
            loading={lineage.isFetchingNextPage}
            onClick={() => void lineage.fetchNextPage()}
            type="button"
          >
            {t("Load earlier runs")}
          </Button>
        )}
      </div>
      <RunEnvironmentMounts key={run.id} run={run} />
      <ThreadMemoryMounts key={`memories-${run.id}`} run={run} />
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

function Attempt({ attempt }: { attempt: Schema["AttemptView"] }) {
  const { t } = useTranslation();
  return (
    <li className={styles.attempt}>
      <span className={styles.attemptHead}>
        <strong>{t("Attempt {{attempt}}", { attempt: attempt.number })}</strong>
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
