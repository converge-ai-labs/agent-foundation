import { ArrowLeftIcon, ArrowRightIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationQueries } from "./api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import styles from "./inspector.module.css";

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
    <section>
      <h3>{t("Events")}</h3>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="code" rows={6} />
      ) : (
        query.data && (
          <>
            {query.data.retained_resource_seq_floor > sequence + 1 && (
              <p className={styles.empty}>
                {t("Earlier lifecycle events have expired from retention.")}
              </p>
            )}
            {!query.data.items.length ? (
              <p className={styles.empty}>{t("No events retained.")}</p>
            ) : (
              <ol className={styles.eventList}>
                {query.data.items.map((event) => (
                  <li key={event.id}>
                    <ModalFrame
                      trigger={
                        <button className={styles.eventRow} type="button">
                          <code>{event.event_type}</code>
                          <span>
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
                size="sm"
                variant="outline"
                disabled={pages.length === 1}
                onClick={() => setPages((previous) => previous.slice(0, -1))}
                type="button"
              >
                <ArrowLeftIcon aria-hidden="true" />
                {t("Previous")}
              </Button>
              <Button
                size="sm"
                variant="outline"
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
                {t("Next")}
                <ArrowRightIcon aria-hidden="true" />
              </Button>
            </div>
          </>
        )
      )}
    </section>
  );
}
