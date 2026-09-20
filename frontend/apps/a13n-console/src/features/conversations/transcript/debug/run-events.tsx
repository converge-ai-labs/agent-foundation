import { CaretLeftIcon, CaretRightIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../../auth/context";
import { useWorkspace } from "../../../../layout/workspace";
import { ErrorNotice, Loading, Timestamp } from "../../../../shared/feedback";
import { JsonView } from "../../../../shared/forms";
import { conversationQueries } from "../../api";
import styles from "./details.module.css";

/** Retained lifecycle events, a page at a time; the payload opens on demand. */
export function RunEvents({ runId }: { runId: string }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    [pages, setPages] = useState([0]);
  const sequence = pages.at(-1)!;
  const query = useQuery({
    ...conversationQueries(client, workspace.id).events(runId, sequence),
    enabled: can("lifecycle_event.read"),
  });
  if (!can("lifecycle_event.read")) return null;
  return (
    <div className={styles.detailBlock}>
      <span className={styles.detailLabel}>{t("Lifecycle events")}</span>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="code" rows={4} />
      ) : (
        query.data && (
          <>
            {query.data.retained_resource_seq_floor > sequence + 1 && (
              <p className={styles.detailNote}>
                {t("Earlier lifecycle events have expired from retention.")}
              </p>
            )}
            {!query.data.items.length ? (
              <p className={styles.detailNote}>{t("No events retained.")}</p>
            ) : (
              <ol className={styles.eventList}>
                {query.data.items.map((event) => (
                  <li key={event.id}>
                    <ModalFrame
                      trigger={
                        <button className={styles.eventEntry} type="button">
                          <span
                            className={styles.eventDot}
                            aria-hidden="true"
                          />
                          <span className={styles.eventType}>
                            {event.event_type}
                          </span>
                          <span className={styles.eventTime}>
                            <Timestamp value={event.occurred_at} relative />
                          </span>
                        </button>
                      }
                      size="lg"
                      title={event.event_type}
                      closeLabel={t("Close")}
                    >
                      <JsonView value={event} />
                    </ModalFrame>
                  </li>
                ))}
              </ol>
            )}
            <div className={styles.eventPager}>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t("Previous")}
                title={t("Previous")}
                disabled={pages.length === 1}
                onClick={() => setPages((previous) => previous.slice(0, -1))}
                type="button"
              >
                <CaretLeftIcon size={14} aria-hidden="true" />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t("Next")}
                title={t("Next")}
                disabled={
                  query.data.next_resource_seq >=
                  query.data.high_watermark_resource_seq
                }
                onClick={() =>
                  setPages((previous) => [
                    ...previous,
                    query.data!.next_resource_seq,
                  ])
                }
                type="button"
              >
                <CaretRightIcon size={14} aria-hidden="true" />
              </Button>
            </div>
          </>
        )
      )}
    </div>
  );
}
