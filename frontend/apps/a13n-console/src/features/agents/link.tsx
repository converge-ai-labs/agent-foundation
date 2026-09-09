import type { ReactNode } from "react";
import { Link } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import { useAgent } from "./queries";

/** Resolve historical IDs before constructing a current key-based link. */
export function AgentLink({
  agentId,
  children,
}: {
  agentId: string;
  children: ReactNode;
}) {
  const agent = useAgent(agentId);
  const { basePath } = useWorkspace();
  return agent.data ? (
    <Link to={`${basePath}/agents/${agent.data.key}`}>{children}</Link>
  ) : (
    <span>{children}</span>
  );
}
