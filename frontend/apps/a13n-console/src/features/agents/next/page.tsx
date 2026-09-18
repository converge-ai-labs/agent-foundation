import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  MenuItem,
  MenuSeparator,
  ModalFrame,
  Tabs,
  TabsList,
  TabsTab,
} from "a13n-ui";
import {
  ArrowLeftIcon,
  DownloadSimpleIcon,
  PencilSimpleIcon,
  PlayIcon,
  SparkleIcon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  representation,
  workspaceHeaders,
} from "../../../shared/api";
import { CopyButton } from "../../../shared/copy";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../../shared/feedback";
import { useIdempotency } from "../../../shared/idempotency";
import { isResourceKey } from "../../../shared/paths";
import { AgentAvatar } from "../avatar";
import type { AgentConfig } from "../configuration";
import { ExportAgent } from "../export";
import { AgentActions, AgentDetails } from "../settings";
import { AgentVersions } from "../versions";
import { AgentEditor, type AgentDraftSummary } from "./editor";
import styles from "./next.module.css";

export function AgentDetailNext() {
  const { agentKey = "" } = useParams(),
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    idempotency = useIdempotency();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") === "versions" ? "versions" : "configuration";
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
        <dl className={styles.summary}>
          <h2>{t("Overview")}</h2>
          <div className={styles.summaryRow}>
            <dt>{t("Status")}</dt>
            <dd>
              <StateBadge state={state} />
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("Default version")}</dt>
            <dd>
              <button
                type="button"
                className={styles.summaryLink}
                onClick={() => setSearch({ tab: "versions" })}
              >
                v{revision.version}
              </button>
              {summary.dirty && (
                <span className="text-muted-foreground">
                  → v{revision.version + 1}
                </span>
              )}
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("Model")}</dt>
            <dd>
              <span className={styles.summaryValue}>
                {summary.modelIcon}
                <span>{summary.modelName}</span>
              </span>
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("Environment")}</dt>
            <dd>
              <span className={styles.summaryValue}>
                <span>{environment}</span>
              </span>
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("Capabilities")}</dt>
            <dd>
              {t("{{count}} skills", { count: summary.skillCount })}
              <span className={styles.dot}>·</span>
              {t("{{count}} connections", { count: summary.connectionCount })}
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("Updated")}</dt>
            <dd>
              <Timestamp value={agent.updated_at} relative />
            </dd>
          </div>
          <div className={styles.summaryRow}>
            <dt>{t("ID")}</dt>
            <dd>
              <code title={agent.id}>{agent.id}</code>
              <CopyButton
                value={agent.id}
                iconOnly
                copyLabel={t("Copy resource ID")}
              />
            </dd>
          </div>
        </dl>
        <p className={styles.railHint}>
          {t(
            "Versions are immutable. Every save creates a new version; older versions stay available in",
          )}{" "}
          <button
            type="button"
            className={styles.summaryLink}
            onClick={() => setSearch({ tab: "versions" })}
          >
            {t("Version history")}
          </button>
          .
        </p>
      </>
    );
  };
  return (
    <div className={styles.page}>
      <Link className={styles.back} to={`${basePath}/agents`}>
        <ArrowLeftIcon size={13} />
        {t("Agents")}
      </Link>
      <header className={styles.header}>
        <div className={styles.identity}>
          <AgentAvatar
            name={agent.name}
            id={agent.id}
            url={agent.image_url}
            className={styles.avatar}
          />
          <div className={styles.titleBlock}>
            <div className={styles.titleRow}>
              <h1>{agent.name}</h1>
              <StateBadge state={state} />
              {can("agent.update") && (
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
              )}
            </div>
            <div className={styles.subtitle}>
              <code>
                {agent.key}
                <CopyButton
                  value={agent.key}
                  iconOnly
                  copyLabel={t("Copy resource key")}
                />
              </code>
              {agent.description && (
                <>
                  <span className={styles.dot}>·</span>
                  <span
                    className={styles.description}
                    title={agent.description}
                  >
                    {agent.description}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className={styles.headerActions}>
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
        </div>
      </header>
      <Tabs
        value={tab}
        onValueChange={(value) =>
          setSearch(value === "versions" ? { tab: "versions" } : {})
        }
        className={styles.tabs}
      >
        <TabsList variant="underline">
          <TabsTab value="configuration">{t("Configuration")}</TabsTab>
          <TabsTab value="versions">
            {t("Versions")}
            <span>v{revision.version}</span>
          </TabsTab>
        </TabsList>
      </Tabs>
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
        <div className={styles.versionsPanel}>
          <AgentVersions
            agent={agent}
            etag={query.data.etag}
            onDefaultChanged={reload}
          />
        </div>
      )}
    </div>
  );
}
