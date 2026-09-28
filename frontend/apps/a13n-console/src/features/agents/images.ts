import type { Client } from "../../service-client";
import { ifMatch, representation } from "../../shared/api";

/** Replace an agent's avatar with `file`, or remove it; the agent's ETag guards either change. */
export function changeAgentImage(
  client: Client,
  workspaceId: string,
  agentId: string,
  etag: string,
  file: File | null,
) {
  const params = { path: { agent_id: agentId } };
  return file
    ? client
        .workspace(workspaceId)
        .PUT("/api/v1/agents/{agent_id}/avatar", {
          params,
          headers: { ...ifMatch(etag), "Content-Type": file.type },
          body: file,
        })
        .then(representation)
    : client
        .workspace(workspaceId)
        .DELETE("/api/v1/agents/{agent_id}/avatar", {
          params,
          headers: ifMatch(etag),
        })
        .then(representation);
}
