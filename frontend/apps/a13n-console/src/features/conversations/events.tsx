import { Button } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationQueries } from "./api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import styles from "./conversations.module.css";

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
      <h3>{t("Lifecycle events")}</h3>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <>
            {query.data.retained_resource_seq_floor > sequence + 1 && (
              <p>
                {t("Earlier lifecycle events have expired from retention.")}
              </p>
            )}
            <JsonView value={query.data.items} />
            <div className={styles.inline}>
              <Button
                size="sm"
                variant="outline"
                disabled={pages.length === 1}
                onClick={() => setPages((previous) => previous.slice(0, -1))}
                type="button"
              >
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
              </Button>
            </div>
          </>
        )
      )}
    </section>
  );
}
