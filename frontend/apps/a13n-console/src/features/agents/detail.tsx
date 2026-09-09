import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { History, Play, Settings } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import agentStyles from "./agents.module.css";
import { initialConfig, type AgentConfig } from "./configuration";
import { AgentForm } from "./form";
import { AgentSettings } from "./settings";
import { AgentVersions } from "./versions";

export function CreateAgent() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    idempotency = useIdempotency();
  const create = useMutation({
    mutationFn: (body: Schema["CreateAgentRequest"]) =>
      client.http
        .POST("/api/v1/workspaces/{workspace_id}/agents", {
          params: {
            path: { workspace_id: workspace.id },
            header: commandHeaders(workspace.id, idempotency.forBody(body)),
          },
          body,
        })
        .then(data),
    onSuccess: (result) => {
      idempotency.reset();
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      navigate(`/workspaces/${workspace.id}/agents/${result.agent.id}`, {
        replace: true,
      });
    },
  });
  return (
    <AgentForm
      back={`/workspaces/${workspace.id}/agents`}
      initial={initialConfig("")}
      creating
      pending={create.isPending}
      error={create.error}
      submit={(config, name, description) =>
        create.mutate({ config, name, description: description || null })
      }
    />
  );
}

export function AgentDetail() {
  const { agentId = "" } = useParams(),
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    idempotency = useIdempotency();
  const [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["agent", workspace.id, agentId],
    queryFn: async ({ signal }) => {
      const resource = representation(
        await client.http.GET("/api/v1/agents/{agent_id}", {
          params: { path: { agent_id: agentId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        }),
      );
      const revision = data(
        await client.http.GET("/api/v1/agent-revisions/{agent_revision_id}", {
          params: {
            path: { agent_revision_id: resource.value.current_revision_id },
          },
          headers: workspaceHeaders(workspace.id),
          signal,
        }),
      );
      return { ...resource, revision };
    },
  });
  const save = useMutation({
    mutationFn: async (body: {
      config: AgentConfig;
      expected_version: number;
    }) => {
      return client.http
        .POST("/api/v1/agents/{agent_id}/revisions", {
          params: {
            path: { agent_id: agentId },
            header: commandHeaders(workspace.id, idempotency.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: async () => {
      idempotency.reset();
      await cache.invalidateQueries({
        queryKey: ["agent", workspace.id, agentId],
      });
      void cache.invalidateQueries({
        queryKey: ["agent-revisions", workspace.id, agentId],
      });
      setGeneration((value) => value + 1);
    },
  });
  if (query.isPending) return <Loading />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const agent = query.data.value;
  const reload = async () => {
    await query.refetch();
    save.reset();
    setGeneration((value) => value + 1);
  };
  return (
    <AgentForm
      key={`${agent.id}:${generation}`}
      back={`/workspaces/${workspace.id}/agents`}
      name={agent.name}
      description={agent.description ?? ""}
      primaryAction={
        can("agent.invoke") && (
          <Button
            variant="default"
            disabled={!agent.enabled || !!agent.archived_at}
            onClick={() =>
              navigate(
                `/workspaces/${workspace.id}/sessions/new?agent=${agent.id}`,
              )
            }
            type="button"
          >
            {<Play size={14} />}
            {t("Try agent")}
          </Button>
        )
      }
      metadata={
        <dl className={agentStyles.metadata}>
          <dt>{t("Status")}</dt>
          <dd>
            <StateBadge
              state={
                agent.archived_at
                  ? "archived"
                  : agent.enabled
                    ? "enabled"
                    : "disabled"
              }
            />
          </dd>
          <dt>{t("Updated")}</dt>
          <dd>
            <Timestamp value={agent.updated_at} relative />
          </dd>
        </dl>
      }
      context={
        <div className={styles.stack}>
          <ModalFrame
            trigger={
              <Button variant="ghost" type="button">
                {<History size={14} />}
                {t("Version history")}
              </Button>
            }
            size={"md"}
            title={t("Version history")}
            description={t("Review and restore saved configurations.")}
            closeLabel={t("Close")}
          >
            <AgentVersions agent={agent} />
          </ModalFrame>
          <ModalFrame
            trigger={
              <Button variant="ghost" type="button">
                {<Settings size={14} />}
                {t("Agent settings")}
              </Button>
            }
            size={"md"}
            title={t("Agent settings")}
            description={t("Manage this agent’s identity and availability.")}
            closeLabel={t("Close")}
          >
            <AgentSettings
              key={`${agent.id}:${generation}`}
              resource={query.data}
              reload={() => void reload()}
            />
          </ModalFrame>
        </div>
      }
      initial={query.data.revision.config}
      version={agent.version}
      pending={save.isPending}
      error={save.error}
      readonly={!can("agent.revision.create")}
      submit={(config, _name, _description, version) => {
        if (version !== undefined)
          save.mutate({ config, expected_version: version });
      }}
      reload={() => void reload()}
    />
  );
}
