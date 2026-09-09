import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationQueries } from "./api";
import { ErrorNotice, StateBadge } from "../../shared/feedback";
import styles from "./conversations.module.css";

export function SteeringStatus({
  runId,
  steerId,
}: {
  runId: string;
  steerId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery({
    ...conversationQueries(client, workspace.id).steer(runId, steerId),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 2000 : false,
  });
  return (
    <div className={styles.notice} role="status">
      {t("Steering")} · {steerId}{" "}
      {query.data && <StateBadge state={query.data.status} />}
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    </div>
  );
}
