import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  MenuItem,
  MenuSeparator,
  ModalFrame,
  StatusPill,
} from "a13n-ui";
import {
  DownloadSimpleIcon,
  PencilSimpleIcon,
  PlayIcon,
  SparkleIcon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  representation,
  workspaceHeaders,
} from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyButton } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import {
  DetailHeader,
  DetailPage,
  RailNote,
  RailRow,
  RailSection,
  useTabParam,
} from "../../shared/page";
import { isResourceKey } from "../../shared/paths";
import { AgentAvatar } from "./avatar";
import type { AgentConfig } from "./configuration";
import { AgentEditor, type AgentDraftSummary } from "./editor";
import { ExportAgent } from "./export";
import { AgentActions, AgentDetails } from "./settings";
import { AgentVersions } from "./versions";
import styles from "./agents.module.css";

export { CreateAgent } from "./create";

export function AgentDetail() {
  const { agentKey = "" } = useParams(),
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    idempotency = useIdempotency();
  const [tab, setTab] = useTabParam(["configuration", "versions"]);
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
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/environment-templates", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
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
        queryKey: ["agent-revisions", workspace.id],
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
  const revision = query.data.revision;
  const editorKey = `${agent.id}:${agent.key}:${generation}`;
  const reload = async () => {
    await query.refetch();
    save.reset();
    setGeneration((value) => value + 1);
  };
  const state = agent.archived_at
    ? "archived"
    : agent.enabled
      ? "enabled"
      : "disabled";
  const rail = (summary: AgentDraftSummary): ReactNode => {
    const environment = summary.environmentId
      ? (templates.data?.find((item) => item.id === summary.environmentId)
          ?.name ?? summary.environmentId)
      : t("None");
    return (
      <>
        <RailSection title={t("Overview")}>
          <RailRow label={t("Status")}>
            <StatePill state={state} />
          </RailRow>
          <RailRow label={t("Default version")}>
            <button
              type="button"
              className={styles.railLink}
              onClick={() => setTab("versions")}
            >
              v{revision.version}
            </button>
            {summary.dirty && (
              <span className="text-muted-foreground">
                → v{revision.version + 1}
              </span>
            )}
          </RailRow>
          <RailRow label={t("Model")}>
            {summary.modelIcon}
            <span>{summary.modelName}</span>
          </RailRow>
          <RailRow label={t("Environment")}>
            <span>{environment}</span>
          </RailRow>
          <RailRow label={t("Capabilities")}>
            {t("{{count}} skills", { count: summary.skillCount })}
            <span className={styles.railDot}>·</span>
            {t("{{count}} connections", { count: summary.connectionCount })}
          </RailRow>
          <RailRow label={t("Updated")}>
            <Timestamp value={agent.updated_at} relative />
          </RailRow>
          <RailRow label={t("ID")}>
            <span title={agent.id}>{agent.id}</span>
            <CopyButton
              value={agent.id}
              iconOnly
              copyLabel={t("Copy resource ID")}
            />
          </RailRow>
        </RailSection>
        <RailNote>
          {t(
            "Versions are immutable. Every save creates a new version; older versions stay available in",
          )}{" "}
          <button
            type="button"
            className={styles.railLink}
            onClick={() => setTab("versions")}
          >
            {t("Version history")}
          </button>
          .
        </RailNote>
      </>
    );
  };
  return (
    <DetailPage
      back={`${basePath}/agents`}
      backLabel={t("Agents")}
      tabs={[
        { value: "configuration", label: t("Configuration") },
        {
          value: "versions",
          label: t("Versions"),
          count: `v${revision.version}`,
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
          status={<StatePill state={state} />}
          resourceKey={agent.key}
          description={agent.description}
          edit={
            can("agent.update") && (
              <ModalFrame
                open={detailsOpen}
                onOpenChange={setDetailsOpen}
                trigger={
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Edit agent details")}
                    title={t("Edit agent details")}
                  >
                    <PencilSimpleIcon size={13} />
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
              {can("agent.update") && (
                <Button
                  variant="outline"
                  onClick={() =>
                    navigate(`${basePath}/configuration/new?agent=${agent.id}`)
                  }
                >
                  <SparkleIcon size={15} aria-hidden="true" />
                  {t("Configure with assistant")}
                </Button>
              )}
              {can("agent.invoke") && (
                <Button
                  variant="default"
                  disabled={!agent.enabled || !!agent.archived_at}
                  onClick={() =>
                    navigate(`${basePath}/sessions/new?agent=${agent.id}`)
                  }
                >
                  <PlayIcon size={14} />
                  {t("Try agent")}
                </Button>
              )}
              <AgentActions
                key={editorKey}
                resource={query.data}
                reload={() => void reload()}
                triggerVariant="outline"
                leading={
                  <>
                    <ExportAgent
                      agent={agent}
                      config={revision.config}
                      version={revision.version}
                      trigger={
                        <MenuItem closeOnClick={false}>
                          <DownloadSimpleIcon size={14} />
                          {t("Export agent")}
                        </MenuItem>
                      }
                    />
                    <MenuSeparator />
                  </>
                }
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
          version={revision.version}
          etag={query.data.etag}
          pending={save.isPending}
          error={save.error}
          readonly={!can("agent.revision.create")}
          submit={(config, etag, note) =>
            save.mutate({ config, etag, change_summary: note })
          }
          discard={() => void reload()}
          rail={rail}
        />
      ) : (
        <AgentVersions
          agent={agent}
          etag={query.data.etag}
          onDefaultChanged={reload}
        />
      )}
    </DetailPage>
  );
}
