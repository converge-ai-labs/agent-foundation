import type { ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { useAgent } from "./queries";

/** Read an agent's current name for a link to its ID. */
export function AgentLink({
  agentId,
  children,
}: {
  agentId: string;
  children?: ReactNode;
}) {
  const agent = useAgent(agentId);
  const { basePath } = useWorkspace();
  const { t } = useTranslation();
  return agent.data && !agent.error ? (
    <Link to={`${basePath}/agents/${agent.data.id}`}>
      {children ?? agent.data.name}
    </Link>
  ) : (
    <span>
      {children ?? t(agent.isPending ? "Loading…" : "Agent unavailable")}
    </span>
  );
}
