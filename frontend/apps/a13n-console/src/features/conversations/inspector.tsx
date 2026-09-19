import { EnvironmentReference } from "../environments/reference";
import { Button, DisclosureSection } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { InfoIcon, XIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId } from "../../shared/copy";
import { JsonView } from "../../shared/form";
import { conversationQueries, isActiveRun, runPath } from "./api";
import styles from "./inspector.module.css";
import { AgentLink } from "../agents/link";
import { RunEvents } from "./events";
import { RunEnvironmentMounts } from "./environment-mounts";
import { useAgent } from "../agents/queries";
import { useRun } from "./queries";

export function RunInspector({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useRun(runId);
  const attempts = useQuery(queries.attempts(runId));
  const lineage = useQuery(queries.lineage(runId));
  const run = runQuery.data;
  return (
    <aside
      id="run-inspector"
      className={styles.panel}
      aria-label={t("Run details")}
    >
      <div className={styles.heading}>
        <InfoIcon size={17} />
        <h2>{t("Run details")}</h2>
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={onClose}
          aria-label={t("Close run details")}
        >
          <XIcon size={15} />
        </Button>
      </div>
      <div className={`${styles.scroll} a13n-scrollbar`}>
        {runQuery.isPending ? (
          <Loading variant="detail" />
        ) : !run ? (
          <ErrorNotice
            error={runQuery.error}
            retry={() => void runQuery.refetch()}
          />
        ) : (
          <div className={styles.inspectorBody}>
            <RunFacts run={run} />
            <RunEnvironmentMounts key={run.id} run={run} />
            <section>
              <h3>{t("Attempts")}</h3>
              <ErrorNotice error={attempts.error ?? lineage.error} />
              {attempts.isPending ? (
                <Loading variant="list" rows={2} />
              ) : !attempts.data?.length ? (
                <p className={styles.empty}>{t("No attempts recorded.")}</p>
              ) : (
                attempts.data.map((attempt) => (
                  <AttemptDetail
                    key={attempt.id}
                    attempt={attempt}
                    bare={attempts.data!.length === 1}
                  />
                ))
              )}
            </section>
            <RunEvents runId={runId} />
            <section>
              <h3>{t("Lineage")}</h3>
              {lineage.data &&
                (!lineage.data.items.length ? (
                  <p className={styles.empty}>{t("No related runs.")}</p>
                ) : (
                  lineage.data.items.map((entry) => (
                    <div key={entry.run_id} className={styles.inspectorLineage}>
                      <Link to={runPath(basePath, entry)}>
                        {t(
                          entry.lineage_kind === "fork"
                            ? "Branch run"
                            : entry.lineage_kind === "continue"
                              ? "Continued run"
                              : "Initial run",
                        )}
                      </Link>
                      <StateBadge state={entry.status} />
                      <small>
                        <Timestamp value={entry.created_at} />
                        <CopyableId value={entry.run_id} />
                      </small>
                    </div>
                  ))
                ))}
            </section>
            <DisclosureSection title={t("Identifiers")}>
              <dl className={styles.metadata}>
                <dt>{t("Run")}</dt>
                <dd>
                  <CopyableId value={run.id} />
                </dd>
                <dt>{t("Agent revision")}</dt>
                <dd>
                  {run.agent_revision_id ? (
                    <AgentLink agentId={run.agent_id}>
                      {run.agent_revision_id}
                    </AgentLink>
                  ) : (
                    t("None")
                  )}
                </dd>
                <dt>{t("Effective configuration digest")}</dt>
                <dd>
                  <CopyableId value={run.effective_agent_config_digest} />
                </dd>
              </dl>
            </DisclosureSection>
            <DisclosureSection title={<>{t("Full run metadata")}</>}>
              <JsonView value={run} />
            </DisclosureSection>
          </div>
        )}
      </div>
    </aside>
  );
}

function RunFacts({ run }: { run: Schema["RunResource"] }) {
  const { t, i18n } = useTranslation();
  const { can, basePath } = useWorkspace();
  const agent = useAgent(run.agent_revision_id ? run.agent_id : undefined);
  const active = isActiveRun(run.status);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || !run.started_at || run.completed_at) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, run.started_at, run.completed_at]);
  const started = run.started_at ? Date.parse(run.started_at) : null;
  const end = run.completed_at
    ? Date.parse(run.completed_at)
    : active
      ? now
      : null;
  const duration = started && end ? (end - started) / 1000 : null;
  return (
    <dl className={styles.runFacts}>
      <dt>{t("Status")}</dt>
      <dd>
        <StateBadge state={run.status} />
      </dd>
      <dt>{t("Agent")}</dt>
      <dd>
        {run.agent_revision_id ? (
          <AgentLink agentId={run.agent_id}>
            {agent.data?.name ?? run.agent_id}
          </AgentLink>
        ) : (
          t("Configuration assistant")
        )}
      </dd>
      <dt>{t("Environment")}</dt>
      <dd>
        {run.environment_id ? (
          <EnvironmentReference id={run.environment_id} />
        ) : (
          t("None")
        )}
      </dd>
      {run.environment_working_directory && (
        <>
          <dt>{t("Working directory")}</dt>
          <dd className="break-all">{run.environment_working_directory}</dd>
        </>
      )}
      <dt>{t("Started")}</dt>
      <dd>{run.started_at ? <Timestamp value={run.started_at} /> : "—"}</dd>
      <dt>{t("Completed")}</dt>
      <dd>{run.completed_at ? <Timestamp value={run.completed_at} /> : "—"}</dd>
      <dt>{t("Duration")}</dt>
      <dd>
        {duration !== null && Number.isFinite(duration)
          ? `${new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 2 }).format(duration)} s`
          : "—"}
      </dd>
      <dt>{t("Trigger source")}</dt>
      <dd>
        {t(`trigger.${run.trigger_type}`, { defaultValue: run.trigger_type })}
      </dd>
      {can("trace.read") && (
        <>
          <dt>{t("Traces")}</dt>
          <dd>
            <Link
              to={`${basePath}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.completed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
            >
              {t("Open run traces")}
            </Link>
          </dd>
        </>
      )}
    </dl>
  );
}

function AttemptDetail({
  attempt,
  bare,
}: {
  attempt: Schema["RunAttemptResource"];
  bare?: boolean;
}) {
  const { t } = useTranslation();
  const body = (
    <>
      <dl className={styles.metadata}>
        {bare && (
          <>
            <dt>{t("Status")}</dt>
            <dd>
              <StateBadge state={attempt.status} />
            </dd>
          </>
        )}
        <dt>{t("Started")}</dt>
        <dd>
          {attempt.started_at ? <Timestamp value={attempt.started_at} /> : "—"}
        </dd>
        <dt>{t("Finished")}</dt>
        <dd>
          {attempt.finished_at ? (
            <Timestamp value={attempt.finished_at} />
          ) : (
            "—"
          )}
        </dd>
        {attempt.start_reason && (
          <>
            <dt>{t("Start reason")}</dt>
            <dd>{attempt.start_reason}</dd>
          </>
        )}
        {attempt.yield_reason && (
          <>
            <dt>{t("Yield reason")}</dt>
            <dd>{attempt.yield_reason}</dd>
          </>
        )}
      </dl>
      {attempt.failure != null && (
        <DisclosureSection title={<>{t("Error details")}</>} defaultOpen>
          <JsonView value={attempt.failure} />
        </DisclosureSection>
      )}
      <DisclosureSection title={<>{t("Raw data")}</>}>
        <JsonView value={attempt} />
      </DisclosureSection>
    </>
  );
  if (bare) return <div className={styles.attempt}>{body}</div>;
  return (
    <DisclosureSection
      defaultOpen={attempt.failure != null}
      title={
        <span className="flex items-center gap-2">
          <span>
            {t("Attempt")} {attempt.attempt_number}
          </span>
          <StateBadge state={attempt.status} />
        </span>
      }
    >
      {body}
    </DisclosureSection>
  );
}
