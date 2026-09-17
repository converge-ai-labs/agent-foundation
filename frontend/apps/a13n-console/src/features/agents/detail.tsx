import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import {
  ClockCounterClockwiseIcon,
  PencilSimpleIcon,
  PlayIcon,
  SparkleIcon,
} from "@phosphor-icons/react";
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
import { isResourceKey } from "../../shared/paths";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import agentStyles from "./agents.module.css";
import { type AgentConfig } from "./configuration";
import { AgentForm } from "./form";
import { AgentActions, AgentDetails } from "./settings";
import { AgentVersions } from "./versions";
import { ExportAgent } from "./export";

export { CreateAgent } from "./create";

export function AgentDetail() {
  const { agentKey = "" } = useParams(),
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    idempotency = useIdempotency();
  const [generation, setGeneration] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const query = useQuery({
    queryKey: ["agent", workspace.id, agentKey],
    enabled: isResourceKey(agentKey),
    queryFn: async ({ signal }) => {
      const resource = representation(
        await client.http.GET("/api/v1/workspaces/{workspace}/agents/{agent}", {
          params: { path: { workspace: workspace.id, agent: agentKey } },
          headers: workspaceHeaders(workspace.id),
          signal,
        }),
      );
      if (!resource.value.default_revision_id)
        throw new Error(t("Agent configuration is unavailable."));
      const revision = data(
        await client.http.GET("/api/v1/agent-revisions/{agent_revision_id}", {
          params: {
            path: { agent_revision_id: resource.value.default_revision_id },
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
      etag?: string;
      change_summary?: string | null;
    }) => {
      if (!body.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const { etag, ...requestBody } = body;
      return client.http
        .POST("/api/v1/workspaces/{workspace}/agents/{agent}/revisions", {
          params: {
            path: { workspace: workspace.id, agent: agentKey },
            header: {
              ...commandHeaders(workspace.id, idempotency.forBody(requestBody)),
              "If-Match": etag,
            },
          },
          body: requestBody,
        })
        .then(data);
    },
    onSuccess: async () => {
      idempotency.reset();
      await cache.invalidateQueries({
        queryKey: ["agent", workspace.id, agentKey],
      });
      void cache.invalidateQueries({
        queryKey: ["agent-revisions", workspace.id, agentKey],
      });
      setGeneration((value) => value + 1);
    },
  });
  if (!isResourceKey(agentKey))
    return <ErrorNotice error={new Error(t("Agent not found"))} />;
  if (query.isPending) return <Loading variant="detail" page />;
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
      key={`${agent.id}:${agent.key}:${generation}`}
      back={`${basePath}/agents`}
      name={agent.name}
      agentId={agent.id}
      agentKey={agent.key}
      imageUrl={agent.image_url}
      description={agent.description ?? ""}
      identityAction={
        can("agent.update") && (
          <ModalFrame
            open={detailsOpen}
            onOpenChange={setDetailsOpen}
            trigger={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t("Edit agent details")}
                title={t("Edit agent details")}
              >
                <PencilSimpleIcon size={14} />
              </Button>
            }
            title={t("Edit agent details")}
            description={t("Update how this agent appears in your workspace.")}
            closeLabel={t("Close")}
          >
            <AgentDetails
              key={`${agent.id}:${agent.key}:${generation}`}
              resource={query.data}
              close={() => setDetailsOpen(false)}
              onImageSaved={async () => {
                await query.refetch();
              }}
              reload={() => void reload()}
            />
          </ModalFrame>
        )
      }
      primaryAction={
        can("agent.invoke") && (
          <Button
            variant="default"
            disabled={!agent.enabled || !!agent.archived_at}
            onClick={() =>
              navigate(`${basePath}/sessions/new?agent=${agent.id}`)
            }
            type="button"
          >
            {<PlayIcon size={14} />}
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
          {can("agent.update") && (
            <Button
              variant="outline"
              onClick={() =>
                navigate(`${basePath}/configuration/new?agent=${agent.id}`)
              }
            >
              <SparkleIcon size={16} aria-hidden="true" />
              {t("Configure with assistant")}
            </Button>
          )}
          <ExportAgent
            agent={agent}
            config={query.data.revision.config}
            version={query.data.revision.version}
          />
          <ModalFrame
            trigger={
              <Button variant="ghost" type="button">
                {<ClockCounterClockwiseIcon size={14} />}
                {t("Version history")}
              </Button>
            }
            size="lg"
            title={t("Version history")}
            description={t(
              "Review saved configurations and choose the default version.",
            )}
            closeLabel={t("Close")}
          >
            <AgentVersions
              agent={agent}
              etag={query.data.etag}
              onDefaultChanged={reload}
            />
          </ModalFrame>
          {(can("agent.lifecycle") || can("agent.duplicate")) && (
            <AgentActions
              key={`${agent.id}:${agent.key}:${generation}`}
              resource={query.data}
              reload={() => void reload()}
            />
          )}
        </div>
      }
      initial={query.data.revision.config}
      version={query.data.revision.version}
      etag={query.data.etag}
      pending={save.isPending}
      error={save.error}
      readonly={!can("agent.revision.create")}
      submit={(config, _name, _description, etag, changeSummary) => {
        save.mutate({ config, etag, change_summary: changeSummary });
      }}
      reload={() => void reload()}
    />
  );
}
