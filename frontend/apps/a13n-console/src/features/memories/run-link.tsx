import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { runPath } from "../conversations/api";
import { useRun } from "../conversations/queries";

/** Resolve the conversation through the authorized run query before offering a link. */
export function MemoryRunLink({
  runId,
  explainUnavailable = false,
}: {
  runId: string;
  explainUnavailable?: boolean;
}) {
  const { basePath } = useWorkspace();
  const { t } = useTranslation();
  const run = useRun(runId);
  return run.data ? (
    <Link to={runPath(basePath, { ...run.data, run_id: run.data.id }, "debug")}>
      {explainUnavailable ? t("View run") : runId}
    </Link>
  ) : (
    <>
      {explainUnavailable
        ? t(run.isPending ? "Loading run…" : "Linked run unavailable")
        : runId}
    </>
  );
}
