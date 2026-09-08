import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
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
    queryKey: ["steer", workspace.id, runId, steerId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/runs/{run_id}/steers/{steer_id}", {
          params: { path: { run_id: runId, steer_id: steerId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
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
