import { AgentAvatar } from "../agents/avatar";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import styles from "./conversations.module.css";
import { useAgent } from "../agents/queries";
import { inputText } from "./input";
import { useRun } from "./queries";

export function SessionIdentity() {
  const { runId, sessionId, threadId } = useParams();
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  const run = useRun(runId);
  const valid =
    run.data?.session_id === sessionId && run.data?.thread_id === threadId;
  const agent = useAgent(
    valid && !run.data?.configuration_draft_id ? run.data?.agent_id : undefined,
  );
  const title = valid
    ? inputText(run.data?.input, run.data?.input_text)
        .replace(/\s+/g, " ")
        .trim()
    : undefined;
  return (
    <div className={styles.sessionIdentity}>
      <h1>{title || t("Session")}</h1>
      {agent.data && (
        <Link to={`${basePath}/agents/${agent.data.key}`}>
          <AgentAvatar
            name={agent.data.name}
            id={run.data?.agent_id}
            url={agent.data.image_url}
            className={styles.identityAvatar}
          />
          {agent.data.name}
        </Link>
      )}
    </div>
  );
}
