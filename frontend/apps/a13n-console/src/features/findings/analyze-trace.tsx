import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";

/** A trace's Service run supplies the target Agent; callers never guess it from span names. */
export function AnalyzeTrace({
  runId,
  traceId,
}: {
  runId: string | null;
  traceId: string;
}) {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["run", workspace.id, runId],
    enabled: !!runId && can("run") && can("write"),
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId! } },
          signal,
        })
        .then(data),
  });
  const run = query.isSuccess ? query.data : undefined;
  if (!run || !["completed", "failed", "cancelled"].includes(run.status))
    return null;
  const search = new URLSearchParams({ trace: traceId });
  return (
    <Button
      variant="outline"
      render={<Link to={`${basePath}/findings?${search}`} />}
    >
      {t("Analyze trace")}
    </Button>
  );
}
