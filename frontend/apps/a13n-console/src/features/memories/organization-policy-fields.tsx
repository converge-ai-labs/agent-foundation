import { useQuery } from "@tanstack/react-query";
import {
  ChoiceField,
  FormField,
  Input,
  Label,
  SearchPicker,
  Switch,
} from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { workspaceModelsQuery } from "../models/api";
import type { PolicyDraft } from "./organization-form";
import { OrganizationHistoryField } from "./organization-history-field";
import styles from "./space-selection.module.css";

export function OrganizationPolicyFields({
  draft,
  change,
  readOnly = false,
}: {
  draft: PolicyDraft;
  change: (value: Partial<PolicyDraft>) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    automaticId = useId();
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  return (
    <div className="grid gap-4">
      <div className={styles.participation}>
        <div>
          <Label htmlFor={automaticId}>{t("Automatic organization")}</Label>
          <p>
            {t(
              "Organize after new memory changes. Each round starts with a fresh thread.",
            )}
          </p>
        </div>
        <Switch
          id={automaticId}
          checked={draft.automatic}
          disabled={readOnly}
          onCheckedChange={(automatic) => change({ automatic })}
        />
      </div>
      <FormField label={t("Organizer model")}>
        <SearchPicker
          label={t("Organizer model")}
          placeholder={t("Choose a model…")}
          emptyMessage={t(
            "No models available. Configure a provider and model first.",
          )}
          value={draft.model}
          onValueChange={(model) => change({ model })}
          disabled={readOnly}
          groups={[
            {
              label: t("Available models"),
              options: (models.data ?? []).map((model) => ({
                value: model.key,
                label: model.name,
                disabled: !model.enabled,
              })),
            },
          ]}
        />
      </FormField>
      <p className="text-muted-foreground text-xs">
        {t("A model is required for automatic and manual organization.")}
      </p>
      <div className="grid gap-4 pt-2">
        <ChoiceField
          label={t("Automatic trigger")}
          value={draft.trigger}
          readOnly={readOnly}
          options={[
            { value: "any_change", label: t("Any saved change") },
            {
              value: "change_count",
              label: t("After a number of changes"),
            },
          ]}
          onValueChange={(trigger) =>
            change({ trigger: trigger as PolicyDraft["trigger"] })
          }
        />
        {draft.trigger === "change_count" && (
          <FormField
            label={t("Changes per round")}
            readOnly={readOnly}
            description={t(
              "Count saved file changes from agents, Console and API. A rename counts once; organizer edits do not count.",
            )}
          >
            <Input
              type="number"
              min={1}
              max={1000000}
              step={1}
              required
              value={draft.changeCount}
              onChange={(event) => change({ changeCount: event.target.value })}
            />
          </FormField>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            label={t("Check interval (seconds)")}
            readOnly={readOnly}
            description={t(
              "How often to check for eligible changes. Minimum 10 seconds.",
            )}
          >
            <Input
              type="number"
              min={10}
              max={86400}
              step={1}
              required
              value={draft.checkInterval}
              onChange={(event) =>
                change({ checkInterval: event.target.value })
              }
            />
          </FormField>
          <FormField
            label={t("Cooldown (seconds)")}
            readOnly={readOnly}
            description={t(
              "Wait after a round ends before starting another. Use 0 for no cooldown.",
            )}
          >
            <Input
              type="number"
              min={0}
              max={604800}
              step={1}
              required
              value={draft.cooldown}
              onChange={(event) => change({ cooldown: event.target.value })}
            />
          </FormField>
        </div>
        <p className="text-muted-foreground text-xs">
          {t(
            "Automatic organization waits for both the trigger and cooldown. Organize now bypasses these limits and the check interval.",
          )}
        </p>
      </div>
      <TextAreaField
        label={t("Task instructions (optional)")}
        value={draft.instructions}
        onChange={(instructions) => change({ instructions })}
        readOnly={readOnly}
        rows={3}
      />
      <OrganizationHistoryField
        value={draft.history}
        onChange={(history) => change({ history })}
        automatic
        readOnly={readOnly}
      />
      <ErrorNotice error={models.error} retry={() => void models.refetch()} />
    </div>
  );
}
