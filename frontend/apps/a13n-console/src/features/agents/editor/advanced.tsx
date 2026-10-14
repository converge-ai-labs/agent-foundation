import { DisclosureSection } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import { advancedConfig } from "../configuration";
import type { AgentDraft } from "./draft";

/** Structured output, retries, and subagents. */
export function AdvancedSection({
  draft,
  readOnly,
  mark,
  expanded,
  onExpandedChange,
  validation,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  /** Says how a read-only view's value differs, beside the title. */
  mark?: ReactNode;
  expanded: boolean;
  onExpandedChange: (value: boolean) => void;
  validation?: Error;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Advanced configuration")}
      description={t("Structured output, retries, and subagents.")}
      actions={mark}
    >
      <DisclosureSection
        open={expanded}
        onOpenChange={onExpandedChange}
        title={t(readOnly ? "Configuration" : "Edit configuration JSON")}
      >
        <TextAreaField
          readOnly={readOnly}
          label={t("Configuration JSON")}
          hideLabel
          code
          value={
            readOnly ? advancedConfig(draft.initial, true) : draft.advanced
          }
          onChange={draft.setAdvanced}
          rows={18}
        />
        <ErrorNotice error={validation} />
      </DisclosureSection>
    </Section>
  );
}
