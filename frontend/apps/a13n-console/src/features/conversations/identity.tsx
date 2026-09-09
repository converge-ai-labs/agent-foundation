import { Link, useParams } from "react-router";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { useRun, useRunAgent } from "./queries";
import styles from "./conversations.module.css";

export function SessionIdentity() {
  const { runId, sessionId, threadId } = useParams();
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  const run = useRun(runId);
  const valid =
    run.data?.session_id === sessionId && run.data?.thread_id === threadId;
  const agent = useRunAgent(valid ? run.data?.agent_id : undefined);
  const title = valid
    ? run.data?.input_text?.replace(/\s+/g, " ").trim()
    : undefined;
  return (
    <div className={styles.sessionIdentity}>
      <h1>{title || t("Session")}</h1>
      {agent.data && (
        <Link to={`/workspaces/${workspace.id}/agents/${agent.data.id}`}>
          <Sparkles size={12} aria-hidden="true" />
          {agent.data.name}
        </Link>
      )}
    </div>
  );
}
