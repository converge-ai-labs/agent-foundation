import { ApiError, type Client } from "../../service-client";
import { allPages, data, workspaceHeaders } from "../../shared/api";
import type { AgentConfig } from "./configuration";

type DependencyKind =
  | "model"
  | "reviewer"
  | "skill"
  | "connection"
  | "agent"
  | "environment_template"
  | "environment_revision"
  | "web"
  | "memory";
export interface AgentDependency {
  path: string;
  kind: DependencyKind;
  value: string;
  version?: number | null;
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
  issue?: string;
}

export function agentDependencies(config: AgentConfig): AgentDependency[] {
  const refs: AgentDependency[] = [
    {
      path: "model.model_key",
      kind: "model",
      value: config.model.model_key,
      replace: (value) => ({
        ...config,
        model: { ...config.model, model_key: value },
      }),
    },
  ];
  for (const [kind, key] of Object.entries(config.media_understanding ?? {}))
    if (key)
      refs.push({
        path: `media_understanding.${kind}`,
        kind: "model",
        value: key,
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
      kind: "reviewer",
      value: reviewer.model,
      replace: (value) => ({
        ...config,
        reviewer: { ...reviewer, model: value },
      }),
    });
  for (const [index, skill] of (config.skills ?? []).entries())
    refs.push({
      path: `skills.${index}.skill_key`,
      kind: "skill",
      value: skill.skill_key,
      version: skill.version,
      replace: (value) => ({
        ...config,
        skills: config.skills?.map((item, i) =>
          i === index ? { ...item, skill_key: value } : item,
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
  for (const [name, child] of Object.entries(config.subagents ?? {})) {
    refs.push({
      path: `subagents.${name}.agent_id`,
      kind: "agent",
      value: child.agent_id,
      version: child.version,
      replace: (value) => ({
        ...config,
        subagents: {
          ...config.subagents,
          [name]: { ...child, agent_id: value },
        },
      }),
    });
    const environment = child.environment;
    if (environment?.template_revision_id)
      refs.push({
        path: `subagents.${name}.environment.template_revision_id`,
        kind: "environment_revision",
        value: environment.template_revision_id,
        replace: (value) => ({
          ...config,
          subagents: {
            ...config.subagents,
            [name]: {
              ...child,
              environment: { ...environment, template_revision_id: value },
            },
          },
        }),
      });
  }
  const memory = config.memory;
  if (memory && "provider_id" in memory)
    refs.push({
      path: "memory.provider_id",
      kind: "memory",
      value: memory.provider_id,
      replace: (value) => ({
        ...config,
        memory: { ...memory, provider_id: value },
      }),
    });
  if (memory && "entries" in memory) {
    memory.entries.forEach((entry, index) => {
      if ("provider_id" in entry.backend)
        refs.push({
          path: `memory.entries.${index}.backend.provider_id`,
          kind: "memory",
          value: entry.backend.provider_id,
          replace: (provider_id) => ({
            ...config,
            memory: {
              entries: memory.entries.map((current, i) =>
                i === index
                  ? { ...current, backend: { provider_id } }
                  : current,
              ),
            },
          }),
        });
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
  workspace: string,
  config: AgentConfig,
  signal?: AbortSignal,
): Promise<DependencyCheck[]> {
  const refs = agentDependencies(config);
  const path = { workspace };
  const headers = workspaceHeaders(workspace);
  function fetchEnvironmentTemplates() {
    return allPages((cursor) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/environment-templates", {
          params: { path, query: { cursor, limit: 100 } },
          headers,
          signal,
        })
        .then(data),
    );
  }
  let environmentTemplates:
    ReturnType<typeof fetchEnvironmentTemplates> | undefined;
  async function choices(kind: DependencyKind): Promise<DependencyOption[]> {
    switch (kind) {
      case "model":
      case "reviewer": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/models", {
              params: { path, query: { cursor, limit: 100, enabled: true } },
              headers,
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => item.enabled)
          .map((item) => ({
            value: kind === "model" ? item.key : item.id,
            label: `${item.name} · ${item.key}`,
            id: item.id,
          }));
      }
      case "skill": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/skills", {
              params: { path, query: { cursor, limit: 100 } },
              headers,
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => !item.deleted_at)
          .map((item) => ({
            value: item.key,
            label: `${item.name} · ${item.key}`,
            id: item.id,
          }));
      }
      case "connection": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/connections", {
              params: { path, query: { cursor, limit: 100 } },
              headers,
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => item.status === "ready")
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.id}`,
            id: item.id,
          }));
      }
      case "agent": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/agents", {
              params: { path, query: { cursor, limit: 100 } },
              headers,
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => item.enabled && !item.archived_at)
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.key}`,
            id: item.id,
          }));
      }
      case "environment_template":
      case "environment_revision": {
        const items = await (environmentTemplates ??=
          fetchEnvironmentTemplates());
        return items
          .filter((item) => !item.archived_at)
          .map((item) => ({
            value:
              kind === "environment_template"
                ? item.id
                : item.default_revision_id,
            label: item.name,
            id: item.id,
          }));
      }
      case "memory": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/memory-providers", {
              params: { path, query: { cursor, limit: 100 } },
              headers,
              signal,
            })
            .then(data),
        );
        return items
          .filter((item) => item.enabled && item.credential_configured)
          .map((item) => ({
            value: item.id,
            label: `${item.name} · ${item.type}`,
            id: item.id,
          }));
      }
      case "web": {
        const items = await allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/web-providers", {
              params: { path, query: { cursor, limit: 100 } },
              headers,
              signal,
            })
            .then(data),
        );
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
      const options = [...(await catalogs.get(ref.kind)!)];
      let selected = options.find((option) => option.value === ref.value);
      if (ref.kind === "environment_revision" && !selected) {
        try {
          const revision = data(
            await client.http.GET(
              "/api/v1/environment-template-revisions/{revision_id}",
              {
                params: { path: { revision_id: ref.value } },
                headers,
                signal,
              },
            ),
          );
          const template = options.find(
            (option) => option.id === revision.template_id,
          );
          if (template) {
            selected = {
              ...template,
              value: ref.value,
              label: `${template.label.split(" · ")[0]} · v${revision.version}`,
            };
            options.push(selected);
          }
        } catch (error) {
          if (
            !(error instanceof ApiError) ||
            ![403, 404].includes(error.status)
          )
            throw error;
        }
      }
      if (!selected)
        return {
          path: ref.path,
          options,
          available: false,
          issue: "Dependency unavailable. Choose a resource in this workspace.",
        };
      if (
        ref.version != null &&
        (ref.kind === "skill" || ref.kind === "agent")
      ) {
        const id = selected.id;
        const revisions =
          ref.kind === "skill"
            ? await allPages((cursor) =>
                client.http
                  .GET("/api/v1/skills/{skill_id}/revisions", {
                    params: {
                      path: { skill_id: id },
                      query: { cursor, limit: 100 },
                    },
                    headers,
                    signal,
                  })
                  .then(data),
              )
            : await allPages((cursor) =>
                client.http
                  .GET(
                    "/api/v1/workspaces/{workspace}/agents/{agent}/revisions",
                    {
                      params: {
                        path: { workspace, agent: id },
                        query: { cursor, limit: 100 },
                      },
                      headers,
                      signal,
                    },
                  )
                  .then(data),
              );
        if (!revisions.some((revision) => revision.version === ref.version))
          return {
            path: ref.path,
            options,
            available: false,
            issue:
              "Pinned version unavailable. Edit the YAML version or choose another resource.",
          };
      }
      return { path: ref.path, options, available: true };
    }),
  );
}
