import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";
import styles from "./editor.module.css";

/** The role, boundaries, and approach the agent follows on every run. */
export function InstructionsSection({
  draft,
  readOnly,
}: {
  draft: AgentDraft;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Instructions")}
      description={t(
        "The role, boundaries, and approach the agent follows on every run.",
      )}
    >
      <TextAreaField
        readOnly={readOnly}
        label={t("System instructions")}
        hideLabel
        value={draft.instructions}
        onChange={draft.setInstructions}
        rows={10}
      />
      <div className={styles.meta}>
        <span>{t("Markdown is supported.")}</span>
        <span>
          {t("{{count}} characters", { count: draft.instructions.length })}
        </span>
      </div>
    </Section>
  );
}
