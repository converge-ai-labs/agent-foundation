import { ClockIcon, PlayIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useAuth, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError, type SessionEvent } from "../../service-client";
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
import { DetailHeader, DetailPage } from "../../shared/page";
import { conversationKeys } from "../conversations/api";
import {
  SessionStreamProvider,
  useSessionStream,
} from "../conversations/session-stream";
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
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const keys = scheduleKeys(workspace.id);
  const query = useQuery(
    scheduleQueries(client, workspace.id).detail(scheduleId),
  );
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(
    () => () => {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = undefined;
    },
    [workspace.id, scheduleId],
  );
  function received(event: SessionEvent) {
    if (event.type === "schedule") {
      // The event is a partial configuration. Keep the full edit representation intact and
      // cancel older reads before fetching it again, so an old invocation never gets a new ETag.
      void cache.cancelQueries({ queryKey: keys.detail(scheduleId) });
      void cache.cancelQueries({ queryKey: keys.status(scheduleId) });
      cache.setQueryData<Schema["ScheduleStatus"]>(
        keys.status(scheduleId),
        (previous) =>
          previous ? { ...previous, ...event.data.progress } : previous,
      );
    } else if (event.type === "occurrence") {
      void cache.cancelQueries({ queryKey: keys.history(scheduleId) });
      cache.setQueriesData<Schema["OccurrencePage"]>(
        { queryKey: keys.history(scheduleId) },
        (previous) =>
          previous
            ? {
                ...previous,
                items: previous.items.map((item) =>
                  item.id === event.data.occurrence.id &&
                  item.version <= event.data.occurrence.version
                    ? event.data.occurrence
                    : item,
                ),
              }
            : previous,
      );
    } else if (event.type === "thread") {
      // Refresh execution observations even when the occurrence itself has not changed.
      void cache.cancelQueries({ queryKey: keys.history(scheduleId) });
    }
    if (event.type === "connection" && event.state === "live") {
      // Every connection starts afresh; the visible historical page can have finished while disconnected.
      void cache.invalidateQueries({
        queryKey: conversationKeys(workspace.id).root,
      });
    }
    // A snapshot can contain many records. Refetch the visible page once after that batch.
    if (
      (event.type === "connection" && event.state === "live") ||
      ["schedule", "occurrence", "thread"].includes(event.type)
    )
      refreshTimer.current ??= setTimeout(() => {
        refreshTimer.current = undefined;
        void cache.invalidateQueries({ queryKey: keys.schedule(scheduleId) });
      }, 50);
  }
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <SessionStreamProvider
      key={query.data.session_id}
      sessionId={query.data.session_id}
      onEvent={received}
    >
      <ScheduleContent schedule={query.data} error={query.error} />
    </SessionStreamProvider>
  );
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
  const stream = useSessionStream(),
    keys = scheduleKeys(workspace.id);
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
  const creator = auth.data?.user.value.id === schedule.principal_id;
  const open = schedule.state !== "archived",
    editable =
      open &&
      schedule.state !== "completed" &&
      creator &&
      can("write") &&
      can("run");
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
          description={schedule.description}
          actions={
            <>
              {can("admin") && <WebhookEditor scheduleId={schedule.id} />}
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
                  {schedule.state !== "completed" && creator && can("run") && (
                    <Button
                      disabled={action.isPending || busy || !status.data}
                      loading={
                        action.isPending && action.variables === "dispatch"
                      }
                      onClick={() => action.mutate("dispatch")}
                    >
                      <PlayIcon size={14} />
                      {t("Run once")}
                    </Button>
                  )}
                  {can("write") && (
                    <Confirm
                      title={t("Archive schedule")}
                      subject={schedule.name}
                      description={t(
                        "Stop future triggers and cancel pending submissions. Existing runs and history remain available.",
                      )}
                      trigger={t("Archive")}
                      triggerVariant="ghost"
                      action={commands.archive}
                      onSuccess={refresh}
                      retry={refresh}
                    />
                  )}
                </>
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
      {stream?.state !== "live" && (
        <p className={styles.notice} role="status">
          {t(
            stream?.state === "closed"
              ? "Live updates stopped"
              : "Connecting to live updates…",
          )}{" "}
          {stream?.state === "closed" && (
            <Button size="sm" variant="outline" onClick={stream.reconnect}>
              {t("Reconnect")}
            </Button>
          )}
        </p>
      )}
      <ErrorNotice error={stream?.error} />
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
      </dl>
      {schedule.state === "completed" && (
        <p className={styles.notice}>
          {t(
            "Scheduling has finished. Check the trigger history for submission and execution results.",
          )}
        </p>
      )}
      {schedule.pause_reason && (
        <p className={styles.notice}>
          {t(`schedule.reason.${schedule.pause_reason}`, {
            defaultValue: schedule.pause_reason,
          })}
        </p>
      )}
      <p className={styles.notice}>
        {t(
          "This schedule uses your saved execution permissions. Signing out or revoking the original API key does not stop it. Pause or archive it to stop future work.",
        )}
      </p>
      <ScheduleHistory scheduleId={schedule.id} />
    </DetailPage>
  );
}
