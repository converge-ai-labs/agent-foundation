import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { AgentDraft } from "./draft";

/** Protocol, input adapter, structured output, retries, and subagents. */
export function AdvancedSection({
  draft,
  readOnly,
  expanded,
  onExpandedChange,
  validation,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  expanded: boolean;
  onExpandedChange: (value: boolean) => void;
  validation?: Error;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Advanced configuration")}
      description={t(
        "Protocol, input adapter, structured output, retries, and subagents.",
      )}
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
          value={draft.advanced}
          onChange={draft.setAdvanced}
          rows={18}
        />
        <ErrorNotice error={validation} />
      </DisclosureSection>
    </Section>
  );
}
