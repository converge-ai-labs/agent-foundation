import type { ReactNode } from "react";
import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";
import styles from "./editor.module.css";

/** The role, boundaries, and approach the agent follows on every run. */
export function InstructionsSection({
  draft,
  readOnly,
  mark,
  officialInstructions,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  /** Says how a read-only view's value differs, beside the title. */
  mark?: ReactNode;
  officialInstructions?: string;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Instructions")}
      description={
        officialInstructions === undefined
          ? t(
              "The role, boundaries, and approach the agent follows on every run.",
            )
          : t(
              "Add requirements for this agent. Official instructions also apply.",
            )
      }
      actions={mark}
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
      {officialInstructions !== undefined && (
        <DisclosureSection title={t("Current official instructions")}>
          <TextAreaField
            readOnly
            label={t("Current official instructions")}
            hideLabel
            hint={t(
              "Official instructions follow the deployed version; they are not saved in this agent version.",
            )}
            value={officialInstructions}
            onChange={() => {}}
          />
        </DisclosureSection>
      )}
    </Section>
  );
}
