import {
  ArchiveIcon,
  DotsThreeOutlineVerticalIcon,
  PlusIcon,
  ClockIcon,
  PlayIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  DisclosureSection,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useAuth, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import type { Schema } from "../../shared/api";
import { Confirm, ConflictNotice } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { DetailHeader, DetailPage, Section } from "../../shared/page";
import { JsonView } from "../../shared/forms";
import { AgentLink } from "../agents/link";
import {
  scheduleCommands,
  scheduleKeys,
  scheduleQueries,
  type Schedule,
} from "./api";
import { WebhookEditor } from "../settings/webhooks/editor";
import { ScheduleEditor } from "./editor";
import { ScheduleHistory } from "./history";
import { ScheduleTiming } from "./timing";
import styles from "./schedules.module.css";

export function ScheduleDetail() {
  const { scheduleId = "" } = useParams(),
    client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery(
    scheduleQueries(client, workspace.id).detail(scheduleId),
  );
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return <ScheduleContent schedule={query.data} error={query.error} />;
}

function ScheduleContent({
  schedule,
  error,
}: {
  schedule: Schedule;
  error: unknown;
}) {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [webhookOpen, setWebhookOpen] = useState(false);
  const keys = scheduleKeys(workspace.id);
  const auth = useAuth();
  const status = useQuery(
    scheduleQueries(client, workspace.id).status(schedule.id),
  );
  const commands = scheduleCommands(client, workspace.id, schedule),
    request = useIdempotency();
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: keys.root });
  };
  const action = useMutation({
    mutationFn: async (command: "enable" | "pause" | "dispatch") => {
      if (command === "dispatch")
        await commands.dispatch(request.forBody({ schedule_id: schedule.id }));
      else await commands[command]();
    },
    onSuccess: () => {
      request.reset();
      refresh();
    },
  });
  const stale = action.error instanceof ApiError && action.error.status === 412;
  const busy =
    !!status.data?.pending_occurrence_id || status.data?.has_unfinished_work;
  const creator = auth.data?.user.value.id === schedule.created_by_id;
  const open = schedule.state !== "archived";
  const editReason =
    schedule.state === "archived"
      ? t("Archived schedules cannot be edited.")
      : schedule.state === "completed"
        ? t("Schedules that have finished cannot be edited.")
        : !creator
          ? t(
              "Only the account that created this schedule can edit it. You are signed in with a different account.",
            )
          : !can("write") || !can("run")
            ? t(
                "Editing this schedule requires both write and run permissions in this workspace.",
              )
            : undefined;
  const editable = !editReason;
  const canDispatch =
    open && schedule.state !== "completed" && creator && can("run");
  const canArchive = open && can("write");
  return (
    <DetailPage
      back={`${basePath}/schedules`}
      backLabel={t("Schedules")}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <ClockIcon size={22} />
            </IconTile>
          }
          name={schedule.name}
          status={
            <StatePill
              state={schedule.state}
              label={
                schedule.state === "completed"
                  ? t("Scheduling finished")
                  : undefined
              }
            />
          }
          resourceKey={schedule.id}
          expandableDescription
          description={
            schedule.description
              ? t("Schedule description: {{description}}", {
                  description: schedule.description,
                })
              : undefined
          }
          actions={
            <>
              {open && (
                <>
                  {editable && <ScheduleEditor schedule={schedule} />}
                  {(schedule.state === "enabled" ? can("write") : editable) && (
                    <Button
                      variant="outline"
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate(
                          schedule.state === "enabled" ? "pause" : "enable",
                        )
                      }
                    >
                      {t(schedule.state === "enabled" ? "Pause" : "Enable")}
                    </Button>
                  )}
                </>
              )}
              {can("admin") && (
                <WebhookEditor
                  scheduleId={schedule.id}
                  open={webhookOpen}
                  onOpenChange={setWebhookOpen}
                  trigger={false}
                />
              )}
              {(can("admin") || canDispatch || canArchive) && (
                <Menu>
                  <MenuTrigger
                    render={
                      <Button
                        variant="outline"
                        size="icon"
                        aria-label={t("More schedule actions")}
                      />
                    }
                  >
                    <DotsThreeOutlineVerticalIcon
                      size={18}
                      weight="fill"
                      aria-hidden="true"
                    />
                  </MenuTrigger>
                  <MenuPopup align="end">
                    {can("admin") && (
                      <MenuItem onClick={() => setWebhookOpen(true)}>
                        <PlusIcon aria-hidden="true" />
                        {t("Add Webhook")}
                      </MenuItem>
                    )}
                    {canDispatch && (
                      <MenuItem
                        disabled={action.isPending || busy || !status.data}
                        onClick={() => action.mutate("dispatch")}
                      >
                        <PlayIcon aria-hidden="true" />
                        {t("Run once")}
                      </MenuItem>
                    )}
                    {canArchive && (
                      <Confirm
                        title={t("Archive schedule")}
                        subject={schedule.name}
                        description={t(
                          "Stop future triggers and cancel pending submissions. Existing runs and history remain available.",
                        )}
                        danger
                        triggerElement={
                          <MenuItem
                            closeOnClick={false}
                            nativeButton
                            render={<button type="button" />}
                            variant="destructive"
                          >
                            <ArchiveIcon aria-hidden="true" />
                            {t("Archive")}
                          </MenuItem>
                        }
                        action={commands.archive}
                        onSuccess={refresh}
                        retry={refresh}
                      />
                    )}
                  </MenuPopup>
                </Menu>
              )}
            </>
          }
        />
      }
    >
      <ErrorNotice error={error ?? status.error} retry={refresh} />
      {stale ? (
        <ConflictNotice
          title={t("Schedule changed")}
          description={t(
            "Reload the current schedule before trying this action again.",
          )}
          recover={{
            label: t("Reload"),
            onClick: () => {
              action.reset();
              refresh();
            },
          }}
        />
      ) : (
        <ErrorNotice error={action.error} />
      )}
      <div>
        <Button variant="outline" onClick={refresh}>
          {t("Refresh")}
        </Button>
      </div>
      {editReason && <p className={styles.notice}>{editReason}</p>}
      <div className={styles.overview}>
        <dl className={styles.summary}>
          <div>
            <dt>{t("Timing")}</dt>
            <dd>
              <ScheduleTiming rule={schedule.schedule} />
            </dd>
          </div>
          <div>
            <dt>{t("Next trigger")}</dt>
            <dd>
              {!status.data ? (
                t("Loading…")
              ) : status.data.next_fire_at ? (
                <Timestamp value={status.data.next_fire_at} />
              ) : (
                t("Not scheduled")
              )}
            </dd>
          </div>
          <div>
            <dt>{t("Work in progress")}</dt>
            <dd>
              {!status.data
                ? t("Loading…")
                : busy
                  ? t("Pending or unfinished work")
                  : t("No unfinished work")}
            </dd>
          </div>
          <div>
            <dt>{t("Target agent")}</dt>
            <dd>
              <AgentLink agentId={schedule.invocation.agent_id} />
            </dd>
            <dd className={styles.resourceId}>
              {schedule.invocation.agent_id}
            </dd>
          </div>
          <div>
            <dt>{t("Thread behavior")}</dt>
            <dd>
              {t(
                schedule.target.mode === "thread"
                  ? "Reuse one thread"
                  : "New thread each time",
              )}
            </dd>
            {schedule.target.thread_id && (
              <dd>
                <Link
                  to={`${basePath}/sessions/${schedule.session_id}/threads/${schedule.target.thread_id}`}
                >
                  {schedule.target.thread_id}
                </Link>
              </dd>
            )}
          </div>
          <div>
            <dt>{t("Target session")}</dt>
            <dd>
              <Link to={`${basePath}/sessions/${schedule.session_id}`}>
                {schedule.session_id}
              </Link>
            </dd>
          </div>
          <div>
            <dt>{t("Agent version")}</dt>
            <dd>{schedule.invocation.agent_revision_id ?? t("Default")}</dd>
          </div>
        </dl>
        {schedule.state === "completed" && (
          <p className={styles.notice}>
            {t(
              "Scheduling has finished. Check the trigger history for submission and execution results.",
            )}
          </p>
        )}
        {schedule.pause_reason && schedule.pause_reason !== "manual" && (
          <p className={styles.notice}>
            {t(`schedule.reason.${schedule.pause_reason}`, {
              defaultValue: schedule.pause_reason,
            })}
          </p>
        )}
      </div>
      <Section
        title={t("Execution instructions")}
        description={t("This input is sent to the agent on every trigger.")}
      >
        <div className={styles.instructions}>
          {schedule.invocation.payload.content.map((part, index) =>
            part.type === "text" ? (
              <div key={index} className={styles.instructionText}>
                {part.text}
              </div>
            ) : part.type === "json" ? (
              <JsonView key={index} value={part.value} />
            ) : (
              <JsonView key={index} value={part} />
            ),
          )}
        </div>
      </Section>
      <DisclosureSection
        className={styles.permissions}
        title={t("Execution permissions")}
      >
        <p className={styles.notice}>
          {t(
            "This schedule uses your saved execution permissions. Signing out or revoking the original API key does not stop it. Pause or archive it to stop future work.",
          )}
        </p>
      </DisclosureSection>
      <div className={styles.history}>
        <ScheduleHistory scheduleId={schedule.id} />
      </div>
    </DetailPage>
  );
}
