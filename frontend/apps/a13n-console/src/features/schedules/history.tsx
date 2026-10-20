import { ClockCounterClockwiseIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Section } from "../../shared/page";
import { scheduleQueries, type Occurrence } from "./api";
import styles from "./schedules.module.css";

/** The submitted receipt is separate from the input's acceptance and its assigned Run. */
function Execution({ occurrence }: { occurrence: Occurrence }) {
  const { t } = useTranslation();
  const execution = occurrence.execution;
  if (!occurrence.entry_id)
    return <span className={styles.notice}>{t("No input submitted")}</span>;
  if (!execution) return <span>{t("Loading…")}</span>;
  return (
    <div>
      {execution.initial_entry_status && (
        <div className={styles.actions}>
          <span className={styles.notice}>{t("Input")}</span>
          <StatePill state={execution.initial_entry_status} />
        </div>
      )}
      {execution.initial_run && (
        <div className={styles.actions}>
          <span className={styles.notice}>{t("Run")}</span>
          <StatePill state={execution.initial_run.status} />
        </div>
      )}
      {!execution.initial_run && (
        <span className={styles.reason}>{t("No run created")}</span>
      )}
      {execution.has_unfinished_work && (
        <span className={styles.reason}>{t("Pending or unfinished work")}</span>
      )}
    </div>
  );
}

export function ScheduleHistory({ scheduleId }: { scheduleId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const page = useCursor({ scheduleId });
  const query = useQuery(
    scheduleQueries(client, workspace.id).history(scheduleId, page.cursor),
  );
  return (
    <Section
      title={t("Trigger history")}
      description={t(
        "Submitted means the input was saved. Input and run states show what happened next.",
      )}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Trigger history")}
            items={query.data.items}
            columns={[
              {
                label: t("Triggered"),
                tone: "primary",
                render: (item) => (
                  <>
                    <Timestamp value={item.scheduled_at ?? item.created_at} />
                    <span className={styles.reason}>
                      {t(
                        item.trigger === "manual"
                          ? "Manual trigger"
                          : "Scheduled trigger",
                      )}
                    </span>
                  </>
                ),
              },
              {
                label: t("Submission"),
                render: (item) => (
                  <>
                    <StatePill state={item.status} />
                    {item.reason && (
                      <span className={styles.reason}>
                        {t(`schedule.reason.${item.reason}`, {
                          defaultValue: item.reason.replaceAll("_", " "),
                        })}
                      </span>
                    )}
                    {item.last_error && (
                      <span className={styles.reason}>{item.last_error}</span>
                    )}
                  </>
                ),
              },
              {
                label: t("Execution"),
                render: (item) => <Execution occurrence={item} />,
              },
              {
                label: t("Attempts"),
                tone: "muted",
                render: (item) => (
                  <>
                    {item.dispatch_attempts}
                    {item.next_attempt_at && (
                      <span className={styles.reason}>
                        {t("Next retry")}{" "}
                        <Timestamp value={item.next_attempt_at} />
                      </span>
                    )}
                  </>
                ),
              },
              {
                label: t("Result"),
                render: (item) =>
                  item.thread_id ? (
                    <Button
                      size="sm"
                      variant="outline"
                      render={
                        <Link
                          to={`${basePath}/sessions/${item.session_id}/threads/${item.thread_id}`}
                        />
                      }
                    >
                      {t("View output")}
                    </Button>
                  ) : (
                    "—"
                  ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} triggers on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<ClockCounterClockwiseIcon size={20} />}
            title={t("No triggers yet")}
            description={t(
              "Enable the schedule or run it once. Its first trigger will appear here.",
            )}
          />
        )
      )}
    </Section>
  );
}
