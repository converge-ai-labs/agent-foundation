import { Button, DisclosureSection, ModalFrame } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { InfoIcon } from "@phosphor-icons/react";
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
import { JsonView } from "../../shared/form";
import { conversationQueries, runPath } from "./api";
import styles from "./inspector.module.css";
import { AgentLink } from "../agents/link";
import { RunEvents } from "./events";
import { useAgent } from "../agents/queries";
import { CopyableId } from "../../shared/copy";

export function RunInspector({ run }: { run: Schema["RunResource"] }) {
  const { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    [open, setOpen] = useState(false);
  const queries = conversationQueries(client, workspace.id);
  const attempts = useQuery({ ...queries.attempts(run.id), enabled: open });
  const lineage = useQuery({ ...queries.lineage(run.id), enabled: open });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button size="sm" variant="outline" type="button">
          {<InfoIcon size={13} />}
          {t("Details")}
        </Button>
      }
      size="lg"
      title={t("Run details")}
      description={t(
        "Inspect the configuration, execution attempts, and related runs.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      <div className={styles.inspectorBody}>
        <RunFacts run={run} />
        <DisclosureSection title={t("Configuration evidence")}>
          {can("trace.read") && (
            <p>
              <Link
                to={`${basePath}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.completed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
              >
                {t("Open run traces")}
              </Link>
            </p>
          )}
          <dl className={styles.metadata}>
            <dt>{t("Run")}</dt>
            <dd>
              <CopyableId value={run.id} />
            </dd>
            <dt>{t("Agent revision")}</dt>
            <dd>
              <AgentLink agentId={run.agent_id}>
                {run.agent_revision_id}
              </AgentLink>
            </dd>
            <dt>{t("Effective configuration digest")}</dt>
            <dd>
              <CopyableId value={run.effective_agent_config_digest} />
            </dd>
            <dt>{t("Environment")}</dt>
            <dd>{run.environment_id ?? t("None")}</dd>
          </dl>
        </DisclosureSection>
        <h3>{t("Attempts")}</h3>
        <ErrorNotice error={attempts.error ?? lineage.error} />
        {attempts.isPending ? (
          <Loading variant="list" rows={3} />
        ) : (
          attempts.data?.map((attempt) => (
            <DisclosureSection
              key={attempt.id}
              title={
                <span className="flex items-center gap-2">
                  <span>
                    {t("Attempt")} {attempt.attempt_number}
                  </span>
                  <StateBadge state={attempt.status} />
                </span>
              }
            >
              <JsonView value={attempt} />
            </DisclosureSection>
          ))
        )}
        <h3>{t("Lineage")}</h3>
        {lineage.data?.items.map((entry) => (
          <div key={entry.run_id} className={styles.inspectorLineage}>
            <Link to={runPath(basePath, entry)} onClick={() => setOpen(false)}>
              {t(
                entry.lineage_kind === "fork"
                  ? "Branch run"
                  : entry.lineage_kind === "continue"
                    ? "Continued run"
                    : "Initial run",
              )}
              <small>
                <Timestamp value={entry.created_at} />
              </small>
            </Link>{" "}
            <StateBadge state={entry.status} />
            <CopyableId value={entry.run_id} />
          </div>
        ))}
        <DisclosureSection title={t("Events")}>
          <RunEvents runId={run.id} />
        </DisclosureSection>
        <DisclosureSection title={<>{t("Full run metadata")}</>}>
          <JsonView value={run} />
        </DisclosureSection>
      </div>
    </ModalFrame>
  );
}

function RunFacts({ run }: { run: Schema["RunResource"] }) {
  const { t, i18n } = useTranslation();
  const agent = useAgent(run.agent_id);
  const duration =
    run.started_at && run.completed_at
      ? (Date.parse(run.completed_at) - Date.parse(run.started_at)) / 1000
      : null;
  return (
    <dl className={styles.runFacts}>
      <dt>{t("Status")}</dt>
      <dd>
        <StateBadge state={run.status} />
      </dd>
      <dt>{t("Agent")}</dt>
      <dd>
        <AgentLink agentId={run.agent_id}>
          {agent.data?.name ?? run.agent_id}
        </AgentLink>
      </dd>
      <dt>{t("Started")}</dt>
      <dd>{run.started_at ? <Timestamp value={run.started_at} /> : "—"}</dd>
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
    </dl>
  );
}
