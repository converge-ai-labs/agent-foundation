import { ClockIcon, PencilSimpleIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, type Schema } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  TextAreaField,
  scheduleInvocation,
} from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import shared from "../../shared/shared.module.css";
import {
  scheduleCommands,
  scheduleKeys,
  scheduleQueries,
  type Schedule,
} from "./api";
import styles from "./schedules.module.css";

export function ScheduleEditor({
  schedule,
  onSaved,
}: {
  schedule?: Schedule;
  onSaved?: (schedule: Schedule) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      size="lg"
      closeLabel={t("Close")}
      title={t(schedule ? "Edit schedule" : "Create schedule")}
      description={t(
        schedule
          ? "Changes apply to future triggers. Existing work keeps its saved input."
          : "New schedules start paused. Preview the timing before enabling.",
      )}
      trigger={
        <Button variant={schedule ? "outline" : "default"} type="button">
          {schedule ? <PencilSimpleIcon size={14} /> : <PlusIcon size={14} />}
          {t(schedule ? "Edit schedule" : "Create schedule")}
        </Button>
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
    { t } = useTranslation();
  // The draft keeps the version it was opened against; live updates cannot silently replace its ETag.
  const [schedule] = useState(initial);
  const [invocationBase, setInvocationBase] = useState<
    Schema["ScheduleInvocation-Input"] | undefined
  >(schedule?.invocation);
  const [name, setName] = useState(schedule?.name ?? "");
  const [description, setDescription] = useState(schedule?.description ?? "");
  const [expression, setExpression] = useState(
    schedule?.schedule.expression ?? "0 9 * * *",
  );
  const [timezone, setTimezone] = useState(
    schedule?.schedule.timezone ??
      Intl.DateTimeFormat().resolvedOptions().timeZone,
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
  const [modeError, setModeError] = useState<unknown>();
  const agents = useQuery(scheduleQueries(client, workspace.id).agents());
  const request = useIdempotency();
  const invocation = (): Schema["ScheduleInvocation-Input"] =>
    mode === "json"
      ? scheduleInvocation(json)
      : {
          ...invocationBase,
          agent_id: agentId,
          agent_revision_id: revision.trim() || null,
          payload: {
            ...invocationBase?.payload,
            content: [{ type: "text", text: input }],
          },
        };
  const save = useMutation({
    mutationFn: async () => {
      const body: Schema["ScheduleCreate"] = {
        name: name.trim(),
        description: description.trim() || null,
        schedule: {
          type: "cron",
          expression: expression.trim(),
          timezone: timezone.trim(),
        },
        max_lateness_seconds: lateness,
        invocation: invocation(),
      };
      return schedule
        ? scheduleCommands(client, workspace.id, schedule).update(body)
        : client
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
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/schedules/preview", {
          body: {
            schedule: {
              expression: expression.trim(),
              timezone: timezone.trim(),
            },
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
      <FormField label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <TextAreaField
        label={t("Description")}
        rows={2}
        value={description}
        onChange={setDescription}
        maxLength={2048}
      />
      <div className={styles.timing}>
        <FormField
          label={t("Cron expression")}
          description={t("Minute, hour, day, month and weekday.")}
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
          description={t("Use an IANA zone, for example Asia/Shanghai.")}
        >
          <Input
            required
            value={timezone}
            onChange={(event) => {
              setTimezone(event.target.value);
              preview.reset();
            }}
          />
        </FormField>
      </div>
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
      {preview.data && (
        <ol className={styles.preview} aria-label={t("Next scheduled times")}>
          {preview.data.items.map((time) => (
            <li key={time.utc}>
              {time.local} <span className={styles.notice}>· {time.utc}</span>
            </li>
          ))}
        </ol>
      )}
      <FormField
        label={t("Maximum delay (seconds)")}
        description={t(
          "Skip a trigger when it cannot be submitted within this time.",
        )}
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
      <SegmentedControl
        label={t("Invocation editor")}
        value={mode}
        onValueChange={(next) => {
          setModeError(undefined);
          if (next === "json") {
            setJson(JSON.stringify(invocation(), null, 2));
            setMode("json");
          } else {
            try {
              const value = scheduleInvocation(json),
                part = value.payload.content[0];
              if (value.payload.content.length !== 1 || part?.type !== "text")
                throw new Error(
                  t(
                    "Fields require one text input. Use JSON to edit structured input.",
                  ),
                );
              setInvocationBase(value);
              setAgentId(value.agent_id);
              setRevision(value.agent_revision_id ?? "");
              setInput(part.text);
              setMode("fields");
            } catch (error) {
              setModeError(error);
            }
          }
        }}
        options={[
          { value: "fields", label: t("Fields"), disabled: !simple },
          { value: "json", label: t("JSON") },
        ]}
      />
      <ErrorNotice error={modeError} />
      {mode === "json" ? (
        <TextAreaField
          label={t("Invocation JSON")}
          code
          rows={12}
          value={json}
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
            onValueChange={setAgentId}
            options={(agents.data ?? []).map((agent) => ({
              value: agent.id,
              label: agent.name,
            }))}
          />
          <FormField
            label={t("Agent revision (optional)")}
            description={t(
              "Leave empty to use the latest revision at each trigger.",
            )}
          >
            <Input
              value={revision}
              onChange={(event) => setRevision(event.target.value)}
            />
          </FormField>
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
        disabled={stale || (mode === "fields" && (!agentId || !input.trim()))}
        label={t(schedule ? "Save changes" : "Create schedule")}
      />
    </form>
  );
}
