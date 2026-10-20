import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice } from "../../shared/feedback";
import { scheduleQueries } from "./api";

export function ScheduleRevisionField({
  agentId,
  value,
  onValueChange,
}: {
  agentId: string;
  value: string;
  onValueChange: (value: string) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const revisions = useQuery(
    scheduleQueries(client, workspace.id).revisions(agentId),
  );
  const options = [
    { value: "", label: t("Default") },
    ...(revisions.data ?? []).map((revision) => ({
      value: revision.id,
      label: `v${revision.number}`,
      hint: revision.note ?? undefined,
    })),
  ];
  // Keep saved/JSON selections visible while loading, or if the list cannot resolve them.
  if (value && !options.some((option) => option.value === value))
    options.push({ value, label: value });
  return (
    <>
      <ChoiceField
        label={t("Agent revision (optional)")}
        value={value}
        onValueChange={onValueChange}
        disabled={!agentId}
        options={options}
        description={
          agentId && revisions.isPending
            ? t("Loading versions…")
            : t(
                "Default uses the agent's current default version for each run.",
              )
        }
      />
      <ErrorNotice
        error={revisions.error}
        retry={() => void revisions.refetch()}
      />
    </>
  );
}
