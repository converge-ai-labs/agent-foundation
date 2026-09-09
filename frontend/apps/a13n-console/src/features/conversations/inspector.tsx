import { Button, DisclosureSection, ModalFrame } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import { conversationQueries, runPath } from "./api";
import styles from "./conversations.module.css";
import { RunEvents } from "./events";

export function RunInspector({ run }: { run: Schema["RunResource"] }) {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
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
          {<Info size={13} />}
          {t("Details")}
        </Button>
      }
      size={"md"}
      title={t("Run details")}
      description={run.id}
      closeLabel={t("Close")}
      open={open}
    >
      <h3>{t("Configuration evidence")}</h3>
      {can("trace.read") && (
        <p>
          <Link
            to={`/workspaces/${workspace.id}/traces?run_id=${run.id}&from=${encodeURIComponent(run.created_at)}&to=${encodeURIComponent(new Date(new Date(run.completed_at ?? Date.now()).getTime() + 1_000).toISOString())}`}
          >
            {t("Open run traces")}
          </Link>
        </p>
      )}
      <dl className={styles.metadata}>
        <dt>{t("Agent revision")}</dt>
        <dd>
          <Link to={`/workspaces/${workspace.id}/agents/${run.agent_id}`}>
            {run.agent_revision_id}
          </Link>
        </dd>
        <dt>{t("Effective configuration digest")}</dt>
        <dd>{run.effective_agent_config_digest}</dd>
        <dt>{t("Environment")}</dt>
        <dd>{run.environment_id ?? t("None")}</dd>
      </dl>
      <h3>{t("Attempts")}</h3>
      <ErrorNotice error={attempts.error ?? lineage.error} />
      {attempts.isPending ? (
        <Loading />
      ) : (
        attempts.data?.map((attempt) => (
          <DisclosureSection
            key={attempt.id}
            title={
              <>
                {t("Attempt")}
                {attempt.attempt_number}
                <StateBadge state={attempt.status} />
              </>
            }
          >
            <JsonView value={attempt} />
          </DisclosureSection>
        ))
      )}
      <h3>{t("Lineage")}</h3>
      {lineage.data?.items.map((entry) => (
        <p key={entry.run_id}>
          <Link
            to={runPath(workspace.id, entry)}
            onClick={() => setOpen(false)}
          >
            {entry.run_id}
          </Link>{" "}
          <StateBadge state={entry.status} />
        </p>
      ))}
      <RunEvents runId={run.id} />
      <DisclosureSection title={<>{t("Full run metadata")}</>}>
        <JsonView value={run} />
      </DisclosureSection>
    </ModalFrame>
  );
}
