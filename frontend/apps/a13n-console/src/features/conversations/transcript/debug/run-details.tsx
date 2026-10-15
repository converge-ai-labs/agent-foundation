import { Button, DisclosureSection, ModalFrame } from "a13n-ui";
import { InfoIcon, MonitorIcon } from "@phosphor-icons/react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../../auth/context";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import {
  ListRow,
  ListRows,
  ListRowsEmpty,
} from "../../../../shared/collection";
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
import { MemoryMountRows } from "../../../memories/mounts";
import { conversationQueries, isActiveRun, runPath } from "../../api";
import { formatDuration } from "../../format";
import { useRun, useRunUsage } from "../../queries";
import { ContextWindow } from "../../context-window";
import { UsageTable } from "../../usage-table";
import {
  computerPermissionMode,
  computerPermissionLabels,
} from "../../computer-permissions";
import styles from "./details.module.css";

type Run = Schema["RunView"];

/**
 * Everything the Run heading does not say, opened from its Details action:
 * what the Run used, how it was configured, its attempts and its lineage.
 */
export function RunDetailsDialog({
  run,
  index,
}: {
  run: Run;
  index: number | null;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      size="xl"
      title={
        <span className={styles.title}>
          {index === null ? t("Run") : t("Run {{index}}", { index })}
          <StatePill state={run.status} />
        </span>
      }
      description={t("Started by {{trigger}}", {
        trigger: t(`trigger.${run.trigger}`, { defaultValue: run.trigger }),
      })}
      closeLabel={t("Close")}
      trigger={
        <Button
          variant="ghost"
          size="sm"
          type="button"
          aria-label={t("Details")}
        >
          <InfoIcon size={14} aria-hidden="true" />
          <span data-action-label>{t("Details")}</span>
        </Button>
      }
    >
      <RunDetails selected={run} />
    </ModalFrame>
  );
}

function RunDetails({ selected }: { selected: Run }) {
  const { t } = useTranslation(),
    { workspace, basePath, can } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const runId = selected.id;
  const runQuery = useRun(runId);
  const usage = useRunUsage(selected);
  const attempts = useQuery(queries.attempts(runId));
  const lineage = useInfiniteQuery(queries.lineage(runId));
  const related = lineage.data?.pages.flatMap((page) => page.items);
  const run = runQuery.data;
  const agent = useAgent(run?.agent_id);
  if (runQuery.isPending) return <Loading variant="detail" />;
  if (!run)
    return (
      <ErrorNotice
        error={runQuery.error}
        retry={() => void runQuery.refetch()}
      />
    );
  const duration =
    run.started_at && run.sealed_at
      ? Date.parse(run.sealed_at) - Date.parse(run.started_at)
      : null;
  const models = usage.data ?? [];
  return (
    <div className={styles.details}>
      <Section title={t("Usage")}>
        <ErrorNotice error={usage.error} retry={() => void usage.refetch()} />
        {usage.isPending ? (
          <Loading variant="list" rows={1} />
        ) : models.length ? (
          <UsageTable
            caption={t("Usage by model")}
            sections={[{ key: "models", groups: [{ key: run.id, models }] }]}
            total
          />
        ) : (
          !usage.error && (
            <p className={styles.note}>{t("No model usage recorded.")}</p>
          )
        )}
      </Section>
      <section className={styles.section}>
        <ContextWindow
          scope={{ run_id: run.id }}
          live={isActiveRun(run.status)}
        />
      </section>
      <Section title={t("Overview")}>
        <dl className={styles.facts}>
          <Fact term={t("Run")}>
            <CopyableId value={run.id} />
          </Fact>
          <Fact term={t("Thread")}>
            <CopyableId value={run.thread_id} />
          </Fact>
          <Fact term={t("Agent")}>
            <AgentLink agentId={run.agent_id}>
              {agent.data?.name ?? run.agent_id}
            </AgentLink>
          </Fact>
          <Fact term={t("Agent revision")}>
            <CopyableId value={run.agent_revision_id} />
          </Fact>
          <Fact term={t("Started")}>
            {run.started_at ? (
              <Timestamp value={run.started_at} precise />
            ) : (
              UNKNOWN
            )}
          </Fact>
          <Fact term={t("Completed")}>
            {run.sealed_at ? (
              <Timestamp value={run.sealed_at} precise />
            ) : (
              UNKNOWN
            )}
          </Fact>
          <Fact term={t("Duration")}>{formatDuration(duration)}</Fact>
          {can("read") && (
            <Fact term={t("Traces")}>
              <Link
                to={`${basePath}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.sealed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
              >
                {t("Open run traces")}
              </Link>
            </Fact>
          )}
        </dl>
      </Section>
      <Section title={t("Environments and memory")}>
        {/* What this Run started with; later Runs follow the conversation's mounts. */}
        {!run.environment_mounts.length && !run.memory_mounts.length ? (
          <ListRowsEmpty>
            {t("No environment or memory mounted.")}
          </ListRowsEmpty>
        ) : (
          <>
            {!!run.environment_mounts.length && (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-muted-foreground">
                  {t(
                    "Permissions captured when this run was accepted. Later changes do not affect this run.",
                  )}
                </p>
                <ListRows>
                  {run.environment_mounts.map((mount) => (
                    <ListRow
                      key={mount.name}
                      icon={<MonitorIcon size={16} />}
                      name={mount.name}
                      secondary={mount.working_directory ?? undefined}
                      control={
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs text-muted-foreground">
                            {t(
                              computerPermissionLabels[
                                computerPermissionMode(
                                  mount.permission_ceiling?.operations ?? [],
                                )
                              ],
                            )}
                          </span>
                          <EnvironmentReference id={mount.environment_id} />
                        </div>
                      }
                    />
                  ))}
                </ListRows>
              </div>
            )}
            {!!run.memory_mounts.length && (
              <MemoryMountRows
                mounts={run.memory_mounts}
                empty={t("None")}
                runId={run.id}
              />
            )}
          </>
        )}
      </Section>
      <Section title={t("Attempts")}>
        <ErrorNotice error={attempts.error} />
        {attempts.isPending ? (
          <Loading variant="list" rows={2} />
        ) : !attempts.data?.length ? (
          <p className={styles.note}>{t("No attempts recorded.")}</p>
        ) : (
          <ol className={styles.list}>
            {attempts.data.map((attempt) => (
              <Attempt key={attempt.id} attempt={attempt} />
            ))}
          </ol>
        )}
      </Section>
      <Section title={t("Lineage")}>
        <ErrorNotice error={lineage.error} />
        {related &&
          (!related.length ? (
            <p className={styles.note}>{t("No related runs.")}</p>
          ) : (
            <ol className={styles.list}>
              {related.map((entry) => (
                <li key={entry.id} className={styles.item}>
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
                  <span className={styles.meta}>
                    <Timestamp value={entry.created_at} relative />
                  </span>
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
      </Section>
      <DisclosureSection title={<>{t("Raw metadata")}</>}>
        <JsonView value={run} />
      </DisclosureSection>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Attempt({ attempt }: { attempt: Schema["AttemptView"] }) {
  const { t } = useTranslation();
  const span =
    attempt.started_at && attempt.finished_at
      ? Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)
      : null;
  // The first attempt starts for the obvious reason; only a later one says why.
  const reasons = [
    attempt.start_reason !== "initial" &&
      t(`attemptStart.${attempt.start_reason}`, {
        defaultValue: attempt.start_reason,
      }),
    attempt.yield_reason,
  ].filter(Boolean);
  return (
    <li className={styles.attempt}>
      <span className={styles.item}>
        <strong>{t("Attempt {{attempt}}", { attempt: attempt.number })}</strong>
        <StatePill state={attempt.status} />
        <span className={styles.meta}>
          <Timestamp value={attempt.started_at} precise />
          {span !== null && ` · ${formatDuration(span)}`}
        </span>
      </span>
      {!!reasons.length && (
        <span className={styles.note}>{reasons.join(" · ")}</span>
      )}
      {attempt.failure != null && (
        <DisclosureSection title={<>{t("Error details")}</>}>
          <JsonView value={attempt.failure} />
        </DisclosureSection>
      )}
    </li>
  );
}
