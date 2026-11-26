import { useTranslation } from "../i18n";
import { acpPresetBrand, ModelIcon } from "../shell/model-identity";
import { SettingsRow } from "../shell/settings";
import { Checkbox } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "../shell/workbench.module.css";

// An inline checklist mirrors ModelSelectionField for configured native and ACP
// agents. Empty selection writes undefined (all configured agents allowed), and
// unavailable IDs stay visible so they can be cleared.
export function AgentSelectionField({
  label,
  description,
  agents,
  models,
  value,
  onChange,
}: {
  label: string;
  description: string;
  agents: Schema<"AgentSummary">[];
  models: Schema<"ModelSummary">[];
  value: string[];
  onChange: (value: string[] | undefined) => void;
}) {
  const { t } = useTranslation();
  const choices = [
    ...agents.map((agent) => {
      const presetBrand =
        agent.kind === "acp_agent" ? acpPresetBrand(agent.preset) : undefined;
      const model =
        agent.kind === "acp_agent"
          ? undefined
          : models.find((item) => item.model_id === agent.model_id);
      return {
        value: agent.agent_id,
        label: agent.name,
        kindLabel: agent.kind === "acp_agent" ? t("ACP agent") : undefined,
        icon: presetBrand ? (
          <ModelIcon modelIdentity={presetBrand} />
        ) : model ? (
          <ModelIcon model={model} />
        ) : undefined,
      };
    }),
    ...value
      .filter((key) => !agents.some((agent) => agent.agent_id === key))
      .map((key) => ({
        value: key,
        label: t("{{id}} (unavailable)", { id: key }),
        kindLabel: undefined,
        icon: undefined,
      })),
  ];
  return (
    <div className={styles.modelSelection}>
      <SettingsRow label={label} description={description} className="pb-1">
        {null}
      </SettingsRow>
      <div
        className={`${styles.selectionList} ${styles.modelSelectionList} a13n-scrollbar`}
        role="group"
        aria-label={label}
      >
        {!choices.length && (
          <p>{t("No options yet. Add a configuration first.")}</p>
        )}
        {choices.map((choice) => (
          <label key={choice.value} className={styles.selectionOption}>
            <Checkbox
              checked={value.includes(choice.value)}
              onCheckedChange={(checked) => {
                const next = checked
                  ? [...value, choice.value]
                  : value.filter((key) => key !== choice.value);
                onChange(next.length ? next : undefined);
              }}
            />
            <span className={styles.agentSelectionLabel}>
              {choice.icon}
              <span className={styles.agentSelectionName}>{choice.label}</span>
              {choice.kindLabel && (
                <span className={styles.agentSelectionKind}>
                  {choice.kindLabel}
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
