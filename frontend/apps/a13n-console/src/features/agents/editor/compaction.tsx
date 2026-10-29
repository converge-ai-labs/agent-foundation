import { FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { formSectionStyles, SwitchField } from "../../../shared/forms";
import type { AgentDraft } from "./draft";

export function CompactionSection({
  draft,
  readOnly,
  error,
}: {
  draft: AgentDraft;
  readOnly: boolean;
  error?: string;
}) {
  const { t } = useTranslation();
  const policy = draft.backgroundCompaction;
  const enabled = policy.enabled ?? false;
  const [threshold, setThreshold] = useState(
    String(Number(((policy.blocking_threshold ?? 0.95) * 100).toFixed(10))),
  );
  return (
    <div data-slot="compaction-fields" className={formSectionStyles.fieldRows}>
      <SwitchField
        label={t("Enabled")}
        readOnly={readOnly}
        checked={enabled}
        onCheckedChange={(enabled) =>
          draft.setBackgroundCompaction({ ...policy, enabled })
        }
      />
      <FormField
        data-field-layout="setting"
        label={t("Wait for compaction at (%)")}
        description={
          !error &&
          t(
            "Wait when input uses this much of the context window. The model may wait sooner to leave room for its reply. Empty uses 95%.",
          )
        }
        error={error}
        readOnly={readOnly}
      >
        <Input
          type="number"
          min={0}
          max={100}
          step="any"
          placeholder="95"
          disabled={!enabled}
          value={threshold}
          onChange={(event) => {
            const value = event.target.value;
            setThreshold(value);
            draft.setBackgroundCompaction({
              ...policy,
              blocking_threshold:
                value === "" ? undefined : Number(value) / 100,
            });
          }}
        />
      </FormField>
    </div>
  );
}
