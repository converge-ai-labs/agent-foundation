import { HeartIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import styles from "./conversations.module.css";
import { useAgent } from "../agents/queries";
import { useRun } from "./queries";

export function SessionIdentity() {
  const { runId, sessionId, threadId } = useParams();
  const { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const run = useRun(runId);
  const valid =
    run.data?.session_id === sessionId && run.data?.thread_id === threadId;
  const agent = useAgent(valid ? run.data?.agent_id : undefined);
  const title = valid
    ? run.data?.input_text?.replace(/\s+/g, " ").trim()
    : undefined;
  return (
    <div className={styles.sessionIdentity}>
      <h1>{title || t("Session")}</h1>
      {agent.data && (
        <Link to={`${basePath}/agents/${agent.data.key}`}>
          <HeartIcon size={12} aria-hidden="true" />
          {agent.data.name}
        </Link>
      )}
    </div>
  );
}
