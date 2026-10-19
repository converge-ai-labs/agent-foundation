import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice } from "../../../shared/feedback";
import { conversationQueries } from "../api";
import { subagentResult } from "../request";
import { requestLabel, RequestContent } from "./user-message";
import styles from "./transcript.module.css";

/**
 * A child Run's result that reached its parent while the parent worked. The
 * model read it as input, but nobody typed it: it reads as the subagent's
 * result from its inbox entry, the same way a Run it starts shows its request.
 */
export function SubagentNotice({
  threadId,
  entryId,
}: {
  threadId?: string;
  entryId?: string;
}) {
  const { t } = useTranslation();
  const client = useClient(),
    { workspace } = useWorkspace();
  const entry = useQuery({
    ...conversationQueries(client, workspace.id).entry(
      threadId ?? "",
      entryId ?? "",
    ),
    enabled: !!threadId && !!entryId,
  });
  const request = entry.data ? subagentResult(entry.data.payload) : null;
  return (
    <section className={styles.subagentNotice}>
      <span className={styles.guidanceLabel}>
        {request ? requestLabel(request, t) : t("Subagent result")}
      </span>
      {request && <RequestContent request={request} />}
      <ErrorNotice error={entry.error} retry={() => void entry.refetch()} />
    </section>
  );
}
