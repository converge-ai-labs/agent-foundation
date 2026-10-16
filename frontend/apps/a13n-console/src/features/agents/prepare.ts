import { ApiError, type Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";

/** Prepare an existing preset before starting a Console conversation with its default revision. */
export async function prepareConversationAgent(
  client: Client,
  workspaceId: string,
  agent: Pick<
    Schema["Agent"],
    "source" | "preset_kind" | "default_revision_id"
  >,
  canWrite: boolean,
): Promise<boolean> {
  if (!canWrite || agent.source !== "builtin") return false;
  const path =
    agent.preset_kind === "composer"
      ? "/api/v1/agent-composer"
      : agent.preset_kind === "finding"
        ? "/api/v1/finding-agent"
        : null;
  if (!path) return false;
  try {
    data(await client.workspace(workspaceId).POST(path, {}));
    return true;
  } catch (error) {
    // Permissions may have changed since Console loaded them. Running still
    // needs server authorization; only preparation falls back to the saved version.
    if (
      agent.default_revision_id &&
      error instanceof ApiError &&
      error.status === 403 &&
      error.code === "forbidden"
    )
      return false;
    throw error;
  }
}
