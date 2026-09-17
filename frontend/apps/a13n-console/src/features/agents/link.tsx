import type { ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { useAgent } from "./queries";

/** Resolve historical IDs before constructing a current key-based link. */
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
    <Link to={`${basePath}/agents/${agent.data.key}`}>
      {children ?? agent.data.name}
    </Link>
  ) : (
    <span>
      {children ?? t(agent.isPending ? "Loading…" : "Agent unavailable")}
    </span>
  );
}
