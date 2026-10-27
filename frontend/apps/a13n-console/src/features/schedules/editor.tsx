import { FormSection } from "../../shared/forms";
import { ClockIcon, PencilSimpleIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import {
  Button,
  Checkbox,
  ChoiceField,
  FormField,
  Input,
  Label,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { useMemo, useRef, useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, type Schema } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  DateTimeField,
  TextAreaField,
  scheduleInvocation,
} from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";
import shared from "../../shared/shared.module.css";
import {
  scheduleCommands,
  scheduleKeys,
  scheduleQueries,
  type Schedule,
} from "./api";
import styles from "./schedules.module.css";
import { JsonDraftSyntaxError } from "./json-draft";
import { invocationFields, type InvocationDraft } from "./invocation-draft";
import { ScheduleRevisionField } from "./revision-field";
import { TimeZonePicker, ZonedTime, describeCron } from "./timing";

/** A whole number of the largest unit that divides the seconds exactly. */
function duration(seconds: number, t: TFunction) {
  if (seconds % 3600 === 0)
    return t("{{count}} hours", { count: seconds / 3600 });
  if (seconds % 60 === 0)
    return t("{{count}} minutes", { count: seconds / 60 });
  return t("{{count}} seconds", { count: seconds });
}

export function ScheduleEditor({
  schedule,
  onSaved,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: ReactElement | false;
  schedule?: Schedule;
  onSaved?: (schedule: Schedule) => void;
}) {
  const { t } = useTranslation(),
    [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = (value: boolean) => {
    setLocalOpen(value);
    onOpenChange?.(value);
  };
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      keepVisibleWhenNested
      size="lg"
      closeLabel={t("Close")}
      title={t(schedule ? "Edit schedule" : "Create schedule")}
      description={t(
        schedule
          ? "Changes apply to future triggers. Existing work keeps its saved input."
          : "Preview the timing, then choose whether to enable the schedule on creation.",
      )}
      trigger={
        trigger === false
          ? undefined
          : (trigger ?? (
              <Button variant={schedule ? "outline" : "default"} type="button">
                {schedule ? (
                  <PencilSimpleIcon size={14} />
                ) : (
                  <PlusIcon size={14} />
                )}
                {t(schedule ? "Edit schedule" : "Create schedule")}
              </Button>
            ))
      }
    >
      {open && (
        <ScheduleForm
          schedule={schedule}
          onCancel={() => setOpen(false)}
          onSaved={(saved) => {
            setOpen(false);
            onSaved?.(saved);
          }}
        />
      )}
    </ModalFrame>
  );
}

function ScheduleForm({
  schedule: initial,
  onCancel,
  onSaved,
}: {
  schedule?: Schedule;
  onCancel: () => void;
  onSaved: (schedule: Schedule) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t, i18n } = useTranslation();
  // The draft keeps the version it was opened against; live updates cannot silently replace its ETag.
  const [schedule] = useState(initial);
  const [invocationBase, setInvocationBase] = useState<
    InvocationDraft | undefined
  >(schedule?.invocation);
  const [name, setName] = useState(schedule?.name ?? "");
  const [description, setDescription] = useState(schedule?.description ?? "");
  const [enableImmediately, setEnableImmediately] = useState(false);
  const cron =
    schedule?.schedule.type === "cron" ? schedule.schedule : undefined;
  const [timingType, setTimingType] = useState(
    schedule?.schedule.type ?? "cron",
  );
  const [originalRunAt] = useState(() => {
    if (schedule?.schedule.type !== "once") return "";
    const date = new Date(schedule.schedule.run_at);
    return formatLocalDateTime(date, "second");
  });
  const [runAt, setRunAt] = useState(originalRunAt);
  const [expression, setExpression] = useState(cron?.expression ?? "0 9 * * *");
  const [timezone, setTimezone] = useState(
    cron?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const [lateness, setLateness] = useState(
    schedule?.max_lateness_seconds ?? 300,
  );
  const [agentId, setAgentId] = useState(schedule?.invocation.agent_id ?? "");
  const [revision, setRevision] = useState(
    schedule?.invocation.agent_revision_id ?? "",
  );
  const firstPart = schedule?.invocation.payload.content[0];
  const [input, setInput] = useState(
    firstPart?.type === "text" ? firstPart.text : "",
  );
  const simple =
    !schedule ||
    (schedule.invocation.payload.content.length === 1 &&
      firstPart?.type === "text");
  const [mode, setMode] = useState(simple ? "fields" : "json");
  const [json, setJson] = useState(
    JSON.stringify(schedule?.invocation ?? {}, null, 2),
  );
  const [fieldsSnapshot, setFieldsSnapshot] = useState<string>();
  const [showModeError, setShowModeError] = useState(false);
  const jsonInput = useRef<HTMLTextAreaElement>(null);
  const parsed = useMemo(() => {
    try {
      return { fields: invocationFields(json) };
    } catch (error) {
      return { error: error as Error };
    }
  }, [json]);
  const agents = useQuery(scheduleQueries(client, workspace.id).agents());
  const request = useIdempotency();
  const timing = (): Schema["ScheduleCreate"]["schedule"] => {
    if (timingType === "once") {
      // Preserve the saved instant's precision and offset when only other fields change.
      if (schedule?.schedule.type === "once" && runAt === originalRunAt)
        return schedule.schedule;
      const date = parseLocalDateTime(runAt);
      if (!date) throw new Error(t("Choose a valid date and time."));
      if (!schedule && date.getTime() <= Date.now())
        throw new Error(
          t("This time has already passed. Choose a future date and time."),
        );
      return { type: "once", run_at: date.toISOString() };
    }
    return {
      type: "cron",
      expression: expression.trim(),
      timezone: timezone.trim(),
    };
  };
  // Field state stays untouched while editing JSON, so discarding restores the exact draft.
  const fieldInvocation = (): InvocationDraft => ({
    ...invocationBase,
    agent_id: agentId,
    agent_revision_id: revision.trim() || null,
    payload: {
      ...invocationBase?.payload,
      content: [
        {
          ...invocationBase?.payload.content[0],
          type: "text",
          text: input,
        },
      ],
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      const body: Schema["ScheduleCreate"] = {
        name: name.trim(),
        description: description.trim() || null,
        schedule: timing(),
        max_lateness_seconds: lateness,
        invocation: scheduleInvocation(
          mode === "json" ? json : JSON.stringify(fieldInvocation()),
        ),
      };
      if (schedule)
        return scheduleCommands(client, workspace.id, schedule).update(body);
      body.state = enableImmediately ? "enabled" : "paused";
      return client
        .workspace(workspace.id)
        .POST("/api/v1/schedules", {
          body,
          params: { header: { "Idempotency-Key": request.forBody(body) } },
        })
        .then(data);
    },
    onSuccess: (saved) => {
      void cache.invalidateQueries({
        queryKey: scheduleKeys(workspace.id).root,
      });
      onSaved(saved);
    },
  });
  const preview = useMutation({
    mutationFn: async () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/schedules/preview", {
          body: {
            schedule: timing(),
            after: new Date().toISOString(),
            count: 5,
          },
        })
        .then(data),
  });
  const stale = save.error instanceof ApiError && save.error.status === 412;
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormSection title={t("Basic information")}>
        <FormField label={t("Name")}>
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <TextAreaField
          label={t("Description (optional)")}
          rows={2}
          value={description}
          onChange={setDescription}
          maxLength={2048}
        />
      </FormSection>
      <FormSection title={t("Schedule")}>
        <SegmentedControl
          label={t("Schedule type")}
          value={timingType}
          onValueChange={(value) => {
            setTimingType(value === "once" ? "once" : "cron");
            preview.reset();
          }}
          options={[
            { value: "cron", label: t("Recurring") },
            { value: "once", label: t("One time") },
          ]}
        />
        {timingType === "once" ? (
          <DateTimeField
            futureOnly={!schedule}
            label={t("Run at")}
            hint={t("Date and time in {{timezone}}.", {
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            })}
            precision="second"
            clearable={false}
            value={runAt}
            onValueChange={(value) => {
              setRunAt(value);
              preview.reset();
            }}
          />
        ) : (
          <div className={styles.timing}>
            <FormField
              label={t("Cron expression")}
              description={
                describeCron(expression, t, i18n.resolvedLanguage) ??
                t("Minute, hour, day, month and weekday.")
              }
            >
              <Input
                required
                value={expression}
                onChange={(event) => {
                  setExpression(event.target.value);
                  preview.reset();
                }}
              />
            </FormField>
            <FormField
              label={t("Time zone")}
              description={t("Times in the expression follow this zone.")}
            >
              <TimeZonePicker
                value={timezone}
                onValueChange={(value) => {
                  setTimezone(value);
                  preview.reset();
                }}
              />
            </FormField>
          </div>
        )}
        <div>
          <Button
            type="button"
            variant="outline"
            loading={preview.isPending}
            onClick={() => preview.mutate()}
          >
            <ClockIcon size={14} />
            {t("Preview next times")}
          </Button>
        </div>
        <ErrorNotice error={preview.error} />
        {preview.data?.items.length === 0 && (
          <p role="status" className={styles.notice}>
            {t(
              timingType === "once"
                ? "This time has already passed. Choose a future date and time."
                : "No upcoming trigger times.",
            )}
          </p>
        )}
        {!!preview.data?.items.length && (
          <ol className={styles.preview} aria-label={t("Next scheduled times")}>
            {preview.data.items.map((time) => (
              <li key={time.utc}>
                <ZonedTime
                  value={time.utc}
                  timeZone={timingType === "once" ? undefined : timezone}
                />
              </li>
            ))}
          </ol>
        )}
        <FormField
          label={t("Maximum delay (seconds)")}
          description={
            Number.isInteger(lateness) && lateness > 0
              ? t(
                  "Skip a trigger that cannot be submitted within {{duration}}.",
                  {
                    duration: duration(lateness, t),
                  },
                )
              : t(
                  "Skip a trigger when it cannot be submitted within this time.",
                )
          }
        >
          <Input
            type="number"
            min={60}
            max={86400}
            required
            value={lateness}
            onChange={(event) => setLateness(event.target.valueAsNumber)}
          />
        </FormField>
      </FormSection>
      <FormSection title={t("Invocation")}>
        <SegmentedControl
          label={t("Invocation editor")}
          value={mode}
          onValueChange={(next) => {
            if (next === mode) return;
            if (next === "json") {
              const snapshot = JSON.stringify(fieldInvocation(), null, 2);
              setFieldsSnapshot(snapshot);
              setJson(snapshot);
              setMode("json");
            } else {
              if (json === fieldsSnapshot) {
                setMode("fields");
                return;
              }
              if (parsed.fields) {
                const { draft, input } = parsed.fields;
                setInvocationBase(draft);
                setAgentId(draft.agent_id);
                setRevision(draft.agent_revision_id ?? "");
                setInput(input);
                setMode("fields");
              } else {
                setShowModeError(true);
              }
            }
          }}
          options={[
            { value: "fields", label: t("Fields") },
            { value: "json", label: t("JSON") },
          ]}
        />
        <ModalFrame
          open={showModeError}
          onOpenChange={setShowModeError}
          finalFocus={jsonInput}
          title={t("Cannot switch to fields")}
          description={parsed.error ? t(parsed.error.message) : undefined}
          closeLabel={t("Close")}
          footer={
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowModeError(false)}
              >
                {t("Continue editing")}
              </Button>
              {fieldsSnapshot !== undefined && (
                <Button
                  type="button"
                  onClick={() => {
                    setJson(fieldsSnapshot);
                    setShowModeError(false);
                    if (!stale) save.reset();
                    setMode("fields");
                  }}
                >
                  {t("Discard JSON changes and return to fields")}
                </Button>
              )}
            </>
          }
        >
          {parsed.error instanceof JsonDraftSyntaxError && (
            <p>
              {t("Line {{line}}, column {{column}}", {
                line: parsed.error.line,
                column: parsed.error.column,
              })}
            </p>
          )}
          {fieldsSnapshot !== undefined && (
            <p className={styles.notice}>
              {t(
                "Restores the form as it was before you opened JSON. Other schedule fields are kept.",
              )}
            </p>
          )}
        </ModalFrame>
        {mode === "json" ? (
          <TextAreaField
            ref={jsonInput}
            label={t("Invocation JSON")}
            code
            rows={12}
            value={json}
            invalid={!!parsed.error}
            onChange={setJson}
          />
        ) : (
          <>
            <ErrorNotice
              error={agents.error}
              retry={() => void agents.refetch()}
            />
            <ChoiceField
              label={t("Agent")}
              required
              value={agentId}
              placeholder={t("Select an agent")}
              onValueChange={(next) => {
                if (next === agentId) return;
                setAgentId(next);
                setRevision("");
              }}
              options={(agents.data ?? []).map((agent) => ({
                value: agent.id,
                label: agent.name,
              }))}
            />
            <ScheduleRevisionField
              agentId={agentId}
              value={revision}
              onValueChange={setRevision}
            />
            <TextAreaField
              label={t("Input")}
              rows={5}
              value={input}
              onChange={setInput}
            />
          </>
        )}
        <p className={styles.notice}>
          {t(
            "This schedule uses your saved execution permissions. Signing out or revoking the original API key does not stop it. Pause or archive it to stop future work.",
          )}
        </p>
      </FormSection>
      {stale ? (
        <ConflictNotice
          title={t("Schedule changed")}
          description={t(
            "Another change was saved. Reload the schedule before editing again.",
          )}
          recover={{
            label: t("Discard and reload"),
            onClick: () => {
              void cache.invalidateQueries({
                queryKey: scheduleKeys(workspace.id).root,
              });
              onCancel();
            },
          }}
        />
      ) : (
        <ErrorNotice error={save.error} />
      )}
      <FormActions
        onCancel={onCancel}
        pending={save.isPending}
        disabled={
          stale ||
          (timingType === "once" && !runAt) ||
          (mode === "fields" && (!agentId || !input.trim()))
        }
        leading={
          !schedule && (
            <Label>
              <Checkbox
                checked={enableImmediately}
                onCheckedChange={setEnableImmediately}
                disabled={save.isPending}
              />
              {t("Enable immediately")}
            </Label>
          )
        }
        label={t(
          schedule
            ? "Save changes"
            : enableImmediately
              ? "Create and enable"
              : "Create schedule",
        )}
      />
    </form>
  );
}
