import { DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { InfoIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { CopyButton, CopyableId } from "../../../shared/identity";
import { JsonView } from "../../../shared/forms";
import { Panel } from "../../../shared/page";
import { AgentLink } from "../../agents/link";
import { useAgent } from "../../agents/queries";
import { EnvironmentReference } from "../../environments/reference";
import { conversationQueries, isActiveRun, runPath } from "../api";
import { useRun } from "../queries";
import { RunEvents } from "./events";
import { RunEnvironmentMounts } from "../environment-mounts";
import styles from "./panels.module.css";

/** Run details beside the transcript, in the shared side-panel anatomy. */
export function RunInspector({
  runId,
  onClose,
  width,
  onWidthChange,
  resizable,
}: {
  runId: string;
  onClose: () => void;
  width?: number;
  onWidthChange?: (width: number) => void;
  resizable?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Panel
      inline
      open
      resizable={resizable}
      width={width}
      onWidthChange={onWidthChange}
      onClose={onClose}
      label={t("Run details")}
      title={
        <>
          <InfoIcon size={15} aria-hidden="true" />
          <strong>{t("Run details")}</strong>
        </>
      }
    >
      <RunDetails runId={runId} />
    </Panel>
  );
}

/** The panel body on its own, for hosts that supply their own container. */
export function RunDetails({ runId }: { runId: string }) {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useRun(runId);
  const attempts = useQuery(queries.attempts(runId));
  const lineage = useQuery(queries.lineage(runId));
  const run = runQuery.data;
  if (runQuery.isPending) return <Loading variant="detail" />;
  if (!run)
    return (
      <ErrorNotice
        error={runQuery.error}
        retry={() => void runQuery.refetch()}
      />
    );
  return (
    <div className={styles.body}>
      <PanelSection title={t("Overview")}>
        <RunFacts run={run} />
      </PanelSection>
      <RunEnvironmentMounts key={run.id} run={run} />
      <PanelSection title={t("Attempts")}>
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
      </PanelSection>
      <RunEvents runId={runId} />
      <PanelSection title={t("Lineage")}>
        {lineage.data &&
          (!lineage.data.items.length ? (
            <p className={styles.empty}>{t("No related runs.")}</p>
          ) : (
            <div className={styles.lineage}>
              {lineage.data.items.map((entry) => (
                <div key={entry.run_id} className={styles.lineageRow}>
                  <Link to={runPath(basePath, entry)}>
                    {t(
                      entry.lineage_kind === "fork"
                        ? "Branch run"
                        : entry.lineage_kind === "continue"
                          ? "Continued run"
                          : "Initial run",
                    )}
                  </Link>
                  <StatePill state={entry.status} />
                  <small>
                    <Timestamp value={entry.created_at} relative />
                  </small>
                  <span className={styles.lineageId}>
                    <CopyButton
                      value={entry.run_id}
                      iconOnly
                      copyLabel={t("Copy resource ID")}
                    />
                  </span>
                </div>
              ))}
            </div>
          ))}
      </PanelSection>
      <DisclosureSection title={t("Identifiers")}>
        <dl className={styles.facts}>
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
      <DisclosureSection title={<>{t("Raw metadata")}</>}>
        <JsonView value={run} />
      </DisclosureSection>
    </div>
  );
}

export function PanelSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.section}>
      <h3>{title}</h3>
      {children}
    </section>
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
    <dl className={styles.facts}>
      <dt>{t("Status")}</dt>
      <dd>
        <StatePill state={run.status} />
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
      <dl className={styles.facts}>
        {bare && (
          <>
            <dt>{t("Status")}</dt>
            <dd>
              <StatePill state={attempt.status} />
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
        <span className={styles.attemptTitle}>
          <span>
            {t("Attempt")} {attempt.attempt_number}
          </span>
          <StatePill state={attempt.status} />
        </span>
      }
    >
      {body}
    </DisclosureSection>
  );
}
