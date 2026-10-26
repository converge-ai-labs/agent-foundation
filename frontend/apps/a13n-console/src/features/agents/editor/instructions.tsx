import type { ReactNode } from "react";
import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";

/** Describe its tasks, how it should respond, and what it should avoid. */
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
              "Describe its tasks, how it should respond, and what it should avoid.",
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
        placeholder={t(
          "For example: You review release notes. Check them against the merged changes, flag risky claims, and keep feedback brief.",
        )}
        value={draft.instructions}
        onChange={draft.setInstructions}
        rows={8}
      />
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
