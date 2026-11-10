import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { runPath } from "../conversations/api";
import { OrganizationCandidate } from "./organization-candidate";
import { organizationQueries } from "./organizations-api";
import styles from "./organization-review.module.css";

/** Deep links carry their workspace and task; the file remains in the URL. */
export function OrganizationReviewPage() {
  const { organizationId = "" } = useParams();
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const task = useQuery(
    organizationQueries(client, workspace.id).task(organizationId),
  );
  if (task.isPending) return <Loading page variant="detail" />;
  if (!task.data)
    return <ErrorNotice error={task.error} retry={() => void task.refetch()} />;
  const value = task.data;
  const target = value.request.target;
  const back = `${basePath}/${target.kind === "memory" ? "memories" : "memory-spaces"}/${target.id}?tab=organization`;
  return (
    <div className={styles.page}>
      <nav
        className={styles.pageNavigation}
        aria-label={t("Organization task navigation")}
      >
        <Link to={back}>
          <ArrowLeftIcon size={14} />
          {t("Back to organization tasks")}
        </Link>
        <Link to={runPath(basePath, value, "debug")}>
          {t("Open run and usage")}
        </Link>
      </nav>
      <details className={styles.taskContext}>
        <summary>{t("Task context")}</summary>
        <div>
          <p className="whitespace-pre-wrap break-words">
            {value.request.instructions}
          </p>
          <p>
            {t("Organizer model")}: {value.request.model} ·{" "}
            {t("Frozen source runs")}: {value.sources.length}
          </p>
          {value.sources.map((source) => (
            <p key={source.run_id}>
              <Link to={runPath(basePath, source, "debug")}>
                {source.run_id}
              </Link>{" "}
              · <Timestamp value={source.sealed_at} />
            </p>
          ))}
        </div>
      </details>
      {value.candidate_memory_id ? (
        <OrganizationCandidate id={value.id} fullHeight />
      ) : (
        <p>
          {t(
            "This task writes directly. Inspect file history for committed changes.",
          )}
        </p>
      )}
    </div>
  );
}
