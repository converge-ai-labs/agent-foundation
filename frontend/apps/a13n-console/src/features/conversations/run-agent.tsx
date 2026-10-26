import { ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { AgentEditor } from "../agents/editor";
import { AgentLink } from "../agents/link";
import { editablePreset, useAgent } from "../agents/queries";
import { ModelKeyLabel } from "../models/model-icon";
import { useRunAgent } from "./queries";
import styles from "./run-agent.module.css";

type Run = Schema["RunView"];

/** The configuration fields this Run's overrides change from its version. */
export function overriddenFields(run: Run) {
  return Object.entries(run.options.overrides ?? {}).flatMap(
    ([field, value]) => (value == null ? [] : [field]),
  );
}

/** The model a Run executed; it opens the Run's whole agent configuration. */
export function RunModelButton({ run }: { run: Run }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const executed = useRunAgent(run.id);
  const overrides = overriddenFields(run).length;
  if (!executed.data) return null;
  return (
    <>
      <button
        type="button"
        className={styles.model}
        aria-haspopup="dialog"
        title={t("View agent configuration")}
        onClick={() => setOpen(true)}
      >
        <ModelKeyLabel
          className={styles.modelLabel}
          modelKey={executed.data.config.model}
        />
        {!!overrides && (
          <span className={styles.overridden}>
            {t("{{count}} overrides", { count: overrides })}
          </span>
        )}
      </button>
      <RunAgentDialog run={run} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * The agent configuration one Run executed, read-only: its version's, with the
 * Run's overrides applied and their sections marked.
 */
export function RunAgentDialog({
  run,
  open,
  onOpenChange,
}: {
  run: Run;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      size="xl"
      open={open}
      onOpenChange={onOpenChange}
      title={t("Agent configuration")}
      description={t(
        "What this run executed. Later versions of the agent do not change it.",
      )}
      closeLabel={t("Close")}
    >
      <RunAgentConfiguration run={run} />
    </ModalFrame>
  );
}

function RunAgentConfiguration({ run }: { run: Run }) {
  const { t } = useTranslation();
  const executed = useRunAgent(run.id);
  const agent = useAgent(run.agent_id);
  if (executed.isPending) return <Loading variant="detail" />;
  if (!executed.data)
    return (
      <ErrorNotice
        error={executed.error}
        retry={() => void executed.refetch()}
      />
    );
  const { revision, config } = executed.data;
  const overridden = overriddenFields(run);
  return (
    <div className="grid gap-6">
      {agent.data && editablePreset(agent.data) && (
        <p>
          {t(
            "This configuration stores your supplemental instructions. Built-in instructions come from the worker version; inspect the model request trace for the text used.",
          )}
        </p>
      )}
      <dl className={styles.facts}>
        <div>
          <dt>{t("Agent")}</dt>
          <dd>
            <AgentLink agentId={run.agent_id} />
          </dd>
        </div>
        <div>
          <dt>{t("Version")}</dt>
          <dd>
            <span className={styles.mono}>v{revision.number}</span>
            <span className={styles.note}>
              {t(`revisionSelection.${run.revision_selection}`)}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("Overrides")}</dt>
          <dd>
            {overridden.length ? (
              <span className={styles.mono}>{overridden.join(", ")}</span>
            ) : (
              <span className={styles.note}>{t("None")}</span>
            )}
          </dd>
        </div>
      </dl>
      <AgentEditor
        initial={config}
        version={revision.number}
        pending={false}
        error={undefined}
        readonly
        submit={() => {}}
        overridden={overridden}
      />
    </div>
  );
}
