import { FormField, Input, Switch } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentDraft } from "./draft";

export function CompactionSection({
  draft,
  readOnly,
}: {
  draft: AgentDraft;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const policy = draft.backgroundCompaction;
  const enabled = policy.enabled ?? false;
  const [threshold, setThreshold] = useState(
    String(Number(((policy.blocking_threshold ?? 0.95) * 100).toFixed(10))),
  );
  return (
    <div className="flex flex-col gap-4">
      <label className="flex items-center gap-2">
        <Switch
          checked={enabled}
          disabled={readOnly}
          onCheckedChange={(enabled) =>
            draft.setBackgroundCompaction({ ...policy, enabled })
          }
        />
        {t("Enable background compaction")}
      </label>
      <FormField
        label={t("Blocking threshold (%)")}
        description={t(
          "Wait for compaction when input reaches this share of the context window. Output capacity can require waiting earlier.",
        )}
        readOnly={readOnly}
      >
        <Input
          className="max-w-32"
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
