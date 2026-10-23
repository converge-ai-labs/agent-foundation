import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";

/** Describe its tasks, how it should respond, and what it should avoid. */
export function InstructionsSection({
  draft,
  readOnly,
  mark,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  /** Says how a read-only view's value differs, beside the title. */
  mark?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Instructions")}
      description={t(
        "Describe its tasks, how it should respond, and what it should avoid.",
      )}
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
    </Section>
  );
}
