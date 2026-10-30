import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
  SettingsRow,
  SettingsSection,
  Switch,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice } from "../../../shared/feedback";
import { FormSection } from "../../../shared/forms";
import { webhookQueries } from "./api";
import {
  lifecycleKinds,
  lifecycleLabels,
  outcomeKinds,
  scheduleOutcomeKinds,
  type SubscriptionDraft,
} from "./draft";
import styles from "./webhooks.module.css";

export function SubscriptionFields({
  draft,
  onChange,
  editing,
  kindsError,
}: {
  draft: SubscriptionDraft;
  onChange: (draft: SubscriptionDraft) => void;
  editing: boolean;
  kindsError?: string;
}) {
  const { t } = useTranslation();
  const set = <K extends keyof SubscriptionDraft>(
    key: K,
    value: SubscriptionDraft[K],
  ) => onChange({ ...draft, [key]: value });
  return (
    <>
      <FormSection title={t("Basic information")}>
        <FormField label={t("Name")}>
          <Input
            autoFocus
            required
            maxLength={128}
            value={draft.name}
            onChange={(event) => set("name", event.target.value)}
            placeholder={t("For example, Run outcomes")}
          />
        </FormField>
        <FormField
          label={t("Endpoint URL")}
          description={t(
            "Use an HTTP(S) address allowed by this Service's outbound policy.",
          )}
        >
          <Input
            type="url"
            required
            maxLength={2048}
            value={draft.url}
            onChange={(event) => set("url", event.target.value)}
            placeholder="https://your-app.example.com/hooks/a13n"
          />
        </FormField>
      </FormSection>
      <FormSection
        title={t("Events")}
        actions={
          <div className={styles.presets}>
            {[
              { label: "Common outcomes", kinds: outcomeKinds },
              { label: "Schedule outcomes", kinds: scheduleOutcomeKinds },
              { label: "All events", kinds: lifecycleKinds },
              { label: "Clear", kinds: [] },
            ].map(({ label, kinds }) => (
              <Button
                key={label}
                type="button"
                size="sm"
                variant="outline"
                onClick={() => set("kinds", [...kinds])}
              >
                {t(label)}
              </Button>
            ))}
          </div>
        }
      >
        <div className={styles.eventGroups}>
          {[
            { prefix: "run.", label: "Run" },
            { prefix: "run_attempt.", label: "Attempt" },
            { prefix: "schedule.", label: "Schedule" },
            { prefix: "schedule_occurrence.", label: "Occurrence" },
          ].map(({ prefix, label }) => (
            <fieldset key={prefix} className={styles.eventGroup}>
              <legend>{t(label)}</legend>
              {[...new Set([...lifecycleKinds, ...draft.kinds])]
                .filter((kind) => kind.startsWith(prefix))
                .map((kind) => (
                  <label className={styles.eventChoice} key={kind}>
                    <Checkbox
                      checked={draft.kinds.includes(kind)}
                      onCheckedChange={(checked) =>
                        set(
                          "kinds",
                          checked
                            ? [...draft.kinds, kind]
                            : draft.kinds.filter((value) => value !== kind),
                        )
                      }
                    />
                    <span>
                      {t(lifecycleLabels[kind] ?? kind)}
                      <code>{kind}</code>
                    </span>
                  </label>
                ))}
            </fieldset>
          ))}
        </div>
        {kindsError && (
          <p className={styles.error} role="alert">
            {kindsError}
          </p>
        )}
        <p className={styles.note}>
          {t(
            "An attempt can fail while its run retries. A run waiting for action has a succeeded attempt.",
          )}
        </p>
        <p className={styles.note}>
          {t(
            "Schedule completion ends scheduling. Occurrence completion ends execution and can report success, failure or cancellation.",
          )}
        </p>
      </FormSection>
      <DisclosureSection
        title={t("Limit notification scope (optional)")}
        defaultOpen={
          !!(
            draft.agentId ||
            draft.sessionId ||
            draft.threadId ||
            draft.scheduleId
          )
        }
      >
        <AgentFilter
          value={draft.agentId}
          onChange={(value) => set("agentId", value)}
        />
        <div className={styles.filterFields}>
          <FormField label={t("Schedule ID (optional)")}>
            <Input
              value={draft.scheduleId}
              onChange={(event) => set("scheduleId", event.target.value)}
              placeholder="sch_…"
            />
          </FormField>
          <FormField label={t("Session ID (optional)")}>
            <Input
              value={draft.sessionId}
              onChange={(event) => set("sessionId", event.target.value)}
              placeholder="sess_…"
            />
          </FormField>
          <FormField label={t("Thread ID (optional)")}>
            <Input
              value={draft.threadId}
              onChange={(event) => set("threadId", event.target.value)}
              placeholder="thread_…"
            />
          </FormField>
        </div>
        <p className={styles.note}>
          {t(
            "All specified conditions must match. Empty fields match the whole workspace.",
          )}
        </p>
      </DisclosureSection>
      <DisclosureSection
        title={t(
          editing
            ? "Replace signing secret (optional)"
            : "Custom signing secret (optional)",
        )}
      >
        <FormField
          label={t("Signing secret")}
          description={t(
            editing
              ? "Leave empty to keep the current secret. Queued deliveries keep their original secret."
              : "Leave empty to generate a secret. It is shown only once after creation.",
          )}
        >
          <Input
            type="password"
            minLength={16}
            maxLength={256}
            autoComplete="new-password"
            value={draft.signingSecret}
            onChange={(event) => set("signingSecret", event.target.value)}
          />
        </FormField>
      </DisclosureSection>
      <SettingsSection variant="outlined">
        <SettingsRow
          label={t(editing ? "Enabled" : "Enable after creation")}
          description={t(
            "Disabling stops new notifications. Queued deliveries continue.",
          )}
          stackOnNarrow={false}
        >
          <Switch
            aria-label={t(editing ? "Enabled" : "Enable after creation")}
            checked={draft.enabled}
            onCheckedChange={(value) => set("enabled", value)}
          />
        </SettingsRow>
      </SettingsSection>
    </>
  );
}

function AgentFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const agents = useQuery(webhookQueries(client, workspace.id).agents());
  const items = agents.data ?? [];
  return (
    <div>
      <FormField label={t("Agent (optional)")}>
        <SearchPicker
          label={t("Agent (optional)")}
          placeholder={t("All agents")}
          emptyMessage={t("No matching agents")}
          value={value || "all"}
          onValueChange={(next) => onChange(next === "all" ? "" : next)}
          groups={[
            {
              label: t("Agents"),
              options: [
                { value: "all", label: t("All agents") },
                ...(value && !items.some((agent) => agent.id === value)
                  ? [
                      {
                        value,
                        label: value,
                        description: t("Saved agent filter"),
                      },
                    ]
                  : []),
                ...items.map((agent) => ({
                  value: agent.id,
                  label: agent.name,
                  description: agent.id,
                })),
              ],
            },
          ]}
        />
      </FormField>
      <ErrorNotice error={agents.error} retry={() => void agents.refetch()} />
    </div>
  );
}
