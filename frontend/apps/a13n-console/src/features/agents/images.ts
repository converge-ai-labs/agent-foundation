import type { Client } from "../../service-client";
import { representation, workspaceHeaders } from "../../shared/api";

export function changeAgentImage(
  client: Client,
  workspace: string,
  agent: string,
  etag: string,
  file: File | null,
) {
  const headers = {
    ...workspaceHeaders(workspace),
    "Content-Type": file?.type ?? "application/octet-stream",
  };
  const params = { path: { workspace, agent }, header: { "If-Match": etag } };
  return file
    ? client.http
        .PUT("/api/v1/workspaces/{workspace}/agents/{agent}/avatar", {
          params,
          headers,
          body: file,
        })
        .then(representation)
    : client.http
        .DELETE("/api/v1/workspaces/{workspace}/agents/{agent}/avatar", {
          params,
          headers,
        })
        .then(representation);
}
