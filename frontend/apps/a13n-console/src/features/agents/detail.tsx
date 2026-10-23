import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import {
  ChatIcon,
  ChatsIcon,
  PencilSimpleIcon,
  PlayIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, representation } from "../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StatePill,
} from "../../shared/feedback";
import { ResourceReference } from "../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../shared/page";
import { useAgentComposer } from "./composer";
import { AgentAvatar } from "./avatar";
import type { AgentConfig } from "./configuration";
import { AgentEditor } from "./editor";
import { editableAgent } from "./queries";
import { AgentActions, AgentDetails } from "./settings";
import { AgentVersions } from "./versions";
import styles from "./agents.module.css";

export { CreateAgent } from "./create";

export function AgentDetail() {
  const { agentId = "" } = useParams(),
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    composer = useAgentComposer();
  const [tab, setTab] = useTabParam(["configuration", "versions"]);
  const [generation, setGeneration] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const query = useQuery({
    queryKey: ["agent", workspace.id, agentId],
    enabled: !!agentId,
    queryFn: async ({ signal }) => {
      const resource = representation(
        await client.workspace(workspace.id).GET("/api/v1/agents/{agent_id}", {
          params: {
            path: { agent_id: agentId },
          },
          signal,
        }),
      );
      if (!resource.value.default_revision_id)
        throw new Error(t("Agent configuration is unavailable."));
      const revision = data(
        await client
          .workspace(workspace.id)
          .GET("/api/v1/agents/{agent_id}/revisions/{revision_id}", {
            params: {
              path: {
                agent_id: resource.value.id,
                revision_id: resource.value.default_revision_id,
              },
            },
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
      note?: string | null;
    }) => {
      if (!body.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const { etag, ...requestBody } = body;
      return client
        .workspace(workspace.id)
        .POST("/api/v1/agents/{agent_id}/revisions", {
          params: { path: { agent_id: agentId } },
          headers: ifMatch(etag),
          body: requestBody,
        })
        .then(data);
    },
    onSuccess: async () => {
      await cache.invalidateQueries({
        queryKey: ["agent", workspace.id, agentId],
      });
      void cache.invalidateQueries({
        queryKey: ["agent-revisions", workspace.id],
      });
      setGeneration((value) => value + 1);
    },
  });
  if (!agentId) return <ErrorNotice error={new Error(t("Agent not found"))} />;
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const agent = query.data.value;
  const revision = query.data.revision;
  const editorKey = `${agent.id}:${generation}`;
  const reload = async () => {
    await query.refetch();
    save.reset();
    setGeneration((value) => value + 1);
  };
  const state = agent.archived_at ? "archived" : "enabled";
  const editable = can("write") && editableAgent(agent);
  return (
    <DetailPage
      back={`${basePath}/agents`}
      backLabel={t("Agents")}
      tabs={[
        { value: "configuration", label: t("Configuration") },
        {
          value: "versions",
          label: t("Versions"),
          count: `v${revision.number}`,
        },
      ]}
      tab={tab}
      onTabChange={setTab}
      header={
        <DetailHeader
          avatar={
            <AgentAvatar
              name={agent.name}
              id={agent.id}
              url={agent.image_url}
              className={styles.detailAvatar}
            />
          }
          name={agent.name}
          status={
            <>
              <ResourceReference id={agent.id} />
              <StatePill state={state} />
            </>
          }
          description={agent.description}
          edit={
            editable && (
              <ModalFrame
                open={detailsOpen}
                onOpenChange={setDetailsOpen}
                trigger={
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Edit agent details")}
                  >
                    <PencilSimpleIcon aria-hidden="true" />
                  </Button>
                }
                title={t("Edit agent details")}
                description={t(
                  "Update how this agent appears in your workspace.",
                )}
                closeLabel={t("Close")}
              >
                <AgentDetails
                  key={editorKey}
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
          actions={
            <>
              {composer.available && (
                <Button
                  variant="outline"
                  loading={composer.pending}
                  onClick={() => composer.start({ agent, revision })}
                >
                  <ChatIcon aria-hidden="true" />
                  {t("Edit with AI")}
                </Button>
              )}
              {can("read") && (
                <Button
                  variant="outline"
                  render={
                    <Link
                      to={`${basePath}/sessions?agent_id=${encodeURIComponent(agent.id)}`}
                    />
                  }
                >
                  <ChatsIcon aria-hidden="true" />
                  {t("View sessions")}
                </Button>
              )}
              {can("run") && (
                <Button
                  variant="default"
                  disabled={!!agent.archived_at}
                  onClick={() =>
                    navigate(`${basePath}/sessions/new?agent=${agent.id}`)
                  }
                >
                  <PlayIcon aria-hidden="true" />
                  {t("Try agent")}
                </Button>
              )}
              <AgentActions
                key={editorKey}
                resource={query.data}
                revision={revision}
                reload={() => void reload()}
              />
            </>
          }
        />
      }
    >
      {tab === "configuration" ? (
        <AgentEditor
          key={editorKey}
          agentId={agent.id}
          initial={revision.config}
          version={revision.number}
          etag={query.data.etag}
          pending={save.isPending}
          error={save.error}
          readonly={!editable}
          submit={(config, etag, note) => save.mutate({ config, etag, note })}
          discard={() => void reload()}
        />
      ) : (
        <AgentVersions
          agent={agent}
          etag={query.data.etag}
          onDefaultChanged={reload}
        />
      )}
      {composer.setup}
      <ErrorToast error={composer.error} />
    </DetailPage>
  );
}
