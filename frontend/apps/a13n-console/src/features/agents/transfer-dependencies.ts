import type { Client } from "../../service-client";
import { allPages, data } from "../../shared/api";
import { connectionState } from "../connections/api";
import { environmentTemplates } from "../environments/api";
import { modelApi } from "../models/api";
import { webProviderApi } from "../web/api";
import type { AgentConfig } from "./configuration";

type DependencyKind =
  | "model"
  | "skill"
  | "connection"
  | "memory"
  | "agent"
  | "environment_template"
  | "web";
export interface AgentDependency {
  path: string;
  kind: DependencyKind;
  value: string;
  /** The pinned revision of a skill or subagent, which belongs to `value` only. */
  revision?: string | null;
  replace: (value: string) => AgentConfig;
}
export interface DependencyOption {
  value: string;
  label: string;
  id: string;
}
export interface DependencyCheck {
  path: string;
  options: DependencyOption[];
  available: boolean;
  /** The pinned revision's number, once found. */
  version?: number;
  issue?: string;
}

/** Choosing another resource drops a pin on a revision of the previous one. */
function pin(current: string, value: string, revision?: string | null) {
  return value === current ? revision : null;
}

export function agentDependencies(config: AgentConfig): AgentDependency[] {
  const refs: AgentDependency[] = [
    {
      path: "model",
      kind: "model",
      value: config.model,
      replace: (value) => ({
        ...config,
        model: value,
      }),
    },
  ];
  for (const [kind, id] of Object.entries(config.media_understanding ?? {}))
    if (id)
      refs.push({
        path: `media_understanding.${kind}`,
        kind: "model",
        value: id,
        replace: (value) => ({
          ...config,
          media_understanding: { ...config.media_understanding, [kind]: value },
        }),
      });
  if (config.default_environment_template_id)
    refs.push({
      path: "default_environment_template_id",
      kind: "environment_template",
      value: config.default_environment_template_id,
      replace: (value) => ({
        ...config,
        default_environment_template_id: value,
      }),
    });
  const reviewer = config.reviewer;
  if (reviewer)
    refs.push({
      path: "reviewer.model",
      kind: "model",
      value: reviewer.model,
      replace: (value) => ({
        ...config,
        reviewer: { ...reviewer, model: value },
      }),
    });
  for (const [index, skill] of (config.skills ?? []).entries())
    refs.push({
      path: `skills.${index}.skill_id`,
      kind: "skill",
      value: skill.skill_id,
      revision: skill.revision_id,
      replace: (value) => ({
        ...config,
        skills: config.skills?.map((item, i) =>
          i === index
            ? {
                ...item,
                skill_id: value,
                revision_id: pin(item.skill_id, value, item.revision_id),
              }
            : item,
        ),
      }),
    });
  for (const [index, connection] of (config.connection_tools ?? []).entries())
    refs.push({
      path: `connection_tools.${index}.connection_id`,
      kind: "connection",
      value: connection.connection_id,
      replace: (value) => ({
        ...config,
        connection_tools: config.connection_tools?.map((item, i) =>
          i === index ? { ...item, connection_id: value } : item,
        ),
      }),
    });
  for (const [index, mount] of (config.memory_mounts ?? []).entries())
    refs.push({
      path: `memory_mounts.${index}.memory_id`,
      kind: "memory",
      value: mount.memory_id,
      replace: (value) => ({
        ...config,
        memory_mounts: config.memory_mounts?.map((item, i) =>
          i === index ? { ...item, memory_id: value } : item,
        ),
      }),
    });
  for (const [name, child] of Object.entries(config.subagents ?? {})) {
    refs.push({
      path: `subagents.${name}.agent_id`,
      kind: "agent",
      value: child.agent_id,
      revision: child.revision_id,
      replace: (value) => ({
        ...config,
        subagents: {
          ...config.subagents,
          [name]: {
            ...child,
            agent_id: value,
            revision_id: pin(child.agent_id, value, child.revision_id),
          },
        },
      }),
    });
    const environment = child.environment;
    if (environment?.template_id)
      refs.push({
        path: `subagents.${name}.environment.template_id`,
        kind: "environment_template",
        value: environment.template_id,
        replace: (value) => ({
          ...config,
          subagents: {
            ...config.subagents,
            [name]: {
              ...child,
              environment: { ...environment, template_id: value },
            },
          },
        }),
      });
  }
  const web = config.toolsets?.web;
  for (const operation of ["search", "scrape"] as const) {
    const tool = web?.tools?.[operation];
    const provider = tool?.config?.provider_id;
    if (typeof provider !== "string") continue;
    refs.push({
      path: `toolsets.web.tools.${operation}.config.provider_id`,
      kind: "web",
      value: provider,
      replace: (value) => ({
        ...config,
        toolsets: {
          ...config.toolsets,
          web: {
            ...web,
            tools: {
              ...web?.tools,
              [operation]: {
                ...tool,
                config: { ...tool?.config, provider_id: value },
              },
            },
          },
        },
      }),
    });
  }
  return refs;
}

export async function inspectAgentDependencies(
  client: Client,
  workspaceId: string,
  config: AgentConfig,
  signal: AbortSignal,
): Promise<DependencyCheck[]> {
  const refs = agentDependencies(config);
  async function choices(kind: DependencyKind): Promise<DependencyOption[]> {
    switch (kind) {
      case "model": {
        const api = modelApi(client, workspaceId);
        const items = await allPages((cursor) => api.models(signal, cursor));
        return items
          .filter((item) => item.enabled)
          .map((item) => ({
            value: item.key,
            label: `${item.name} · ${item.key}`,
            id: item.key,
          }));
      }
      case "skill": {
        const items = await allPages((cursor) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/skills", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => !item.archived_at)
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.id}`,
            id: item.id,
          }));
      }
      case "connection": {
        const items = await allPages((cursor) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/connections", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => connectionState(item) === "ready")
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.id}`,
            id: item.id,
          }));
      }
      case "memory": {
        const items = await allPages((cursor) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/memories", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        );
        return items.map((item) => ({
          value: item.id,
          label: `${item.name} · ${item.id}`,
          id: item.id,
        }));
      }
      case "agent": {
        const items = await allPages((cursor) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/agents", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => !item.archived_at)
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.id}`,
            id: item.id,
          }));
      }
      case "environment_template": {
        const items = await allPages((cursor) =>
          environmentTemplates(client, workspaceId, signal, cursor),
        );
        return items
          .filter((item) => item.enabled)
          .map((item) => ({ value: item.id, label: item.name, id: item.id }));
      }
      case "web": {
        const api = webProviderApi(client, workspaceId);
        const items = await allPages((cursor) => api.providers(signal, cursor));
        return items
          .filter((item) => item.enabled && item.credential_configured)
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.type}`,
            id: item.id,
          }));
      }
    }
  }
  const catalogs = new Map<DependencyKind, Promise<DependencyOption[]>>();
  for (const { kind } of refs)
    if (!catalogs.has(kind)) catalogs.set(kind, choices(kind));
  return Promise.all(
    refs.map(async (ref): Promise<DependencyCheck> => {
      const options = await catalogs.get(ref.kind)!;
      const selected = options.find((option) => option.value === ref.value);
      if (!selected)
        return {
          path: ref.path,
          options,
          available: false,
          issue: "Dependency unavailable. Choose a resource in this workspace.",
        };
      if (ref.revision && (ref.kind === "skill" || ref.kind === "agent")) {
        const id = selected.id;
        const revisions =
          ref.kind === "skill"
            ? await allPages((cursor) =>
                client
                  .workspace(workspaceId)
                  .GET("/api/v1/skills/{skill_id}/revisions", {
                    params: {
                      path: { skill_id: id },
                      query: { cursor, limit: 100 },
                    },
                    signal,
                  })
                  .then(data),
              )
            : await allPages((cursor) =>
                client
                  .workspace(workspaceId)
                  .GET("/api/v1/agents/{agent_id}/revisions", {
                    params: {
                      path: { agent_id: id },
                      query: { cursor, limit: 100 },
                    },
                    signal,
                  })
                  .then(data),
              );
        const pinned = revisions.find(
          (revision) => revision.id === ref.revision,
        );
        if (!pinned)
          return {
            path: ref.path,
            options,
            available: false,
            issue:
              "Pinned version unavailable. Edit the YAML version or choose another resource.",
          };
        return {
          path: ref.path,
          options,
          available: true,
          version: pinned.number,
        };
      }
      return { path: ref.path, options, available: true };
    }),
  );
}
