import type { Client } from "@converge.ai/a13n";
import { data, type Schema } from "../../shared/api";

export type MemoryTarget = {
  workspace: string;
  provider_id: string;
  scope: "user" | "agent" | "thread";
  subject_id?: string;
};

export function memoryKey(target: MemoryTarget) {
  return [
    "memories",
    target.workspace,
    target.provider_id,
    target.scope,
    target.scope === "user" ? null : target.subject_id,
  ] as const;
}

export function memoriesPath(
  basePath: string,
  selection?: Pick<MemoryTarget, "scope" | "subject_id"> & {
    provider_id?: string;
  },
) {
  const params = new URLSearchParams();
  params.set("scope", selection?.scope ?? "user");
  if (selection?.provider_id) params.set("provider", selection.provider_id);
  if (selection?.scope !== "user" && selection?.subject_id)
    params.set("subject", selection.subject_id);
  return `${basePath}/memories?${params}`;
}

export function memoryApi(client: Client, target: MemoryTarget) {
  const path = { workspace: target.workspace, provider_id: target.provider_id };
  const query = {
    scope: target.scope,
    subject_id: target.scope === "user" ? undefined : target.subject_id,
  };
  return {
    access: (signal: AbortSignal) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memory-access",
          { params: { path, query }, signal },
        )
        .then(data),
    list: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories",
          {
            params: { path, query: { ...query, limit: 1000, cursor } },
            signal,
          },
        )
        .then(data),
    search: (body: Schema["MemorySearch"], signal: AbortSignal) =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/search",
          { params: { path, query }, body, signal },
        )
        .then(data),
    get: (memory_id: string, signal?: AbortSignal) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/{memory_id}",
          { params: { path: { ...path, memory_id }, query }, signal },
        )
        .then(data),
    add: (text: string) =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories",
          { params: { path, query }, body: { text } },
        )
        .then(data),
    update: (memory_id: string, text: string) =>
      client.http
        .PUT(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/{memory_id}",
          { params: { path: { ...path, memory_id }, query }, body: { text } },
        )
        .then(data),
    remove: (memory_id: string) =>
      client.http
        .DELETE(
          "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/{memory_id}",
          { params: { path: { ...path, memory_id }, query } },
        )
        .then(() => undefined),
  };
}
