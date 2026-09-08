import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, SelectField, Tabs } from "a13n-ui";
import { Play, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  commandHeaders,
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, Loading, Page, Timestamp } from "../../shared/feedback";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { Pagination, Table, useCursor } from "../../shared/collection";
import { AgentForm } from "./form";
import { initialConfig, type AgentConfig } from "./configuration";
import styles from "../../shared/shared.module.css";

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
    <Page
      title={t("Create agent")}
      description={t("Start with clear instructions and the right model.")}
      back={`/workspaces/${workspace.id}/agents`}
    >
      <AgentForm
        initial={initialConfig("")}
        creating
        pending={create.isPending}
        error={create.error}
        submit={(config, name, description) =>
          create.mutate({ config, name, description: description || null })
        }
      />
    </Page>
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
    <Page
      title={agent.name}
      description={
        agent.description ?? t("Configure, version, and run this agent.")
      }
      back={`/workspaces/${workspace.id}/agents`}
      actions={
        can("agent.invoke") && (
          <Button
            variant="primary"
            icon={<Play size={14} />}
            disabled={!agent.enabled || !!agent.archived_at}
            onClick={() =>
              navigate(
                `/workspaces/${workspace.id}/sessions/new?agent=${agent.id}`,
              )
            }
          >
            {t("Try agent")}
          </Button>
        )
      }
    >
      <Tabs
        label={t("Agent details")}
        defaultValue="configuration"
        items={[
          {
            value: "configuration",
            label: t("Configuration"),
            content: (
              <AgentForm
                key={`${agent.id}:${generation}`}
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
            ),
          },
          {
            value: "versions",
            label: t("Versions"),
            content: <AgentVersions agent={agent} />,
          },
          {
            value: "settings",
            label: t("Settings"),
            content: (
              <AgentSettings
                key={`${agent.id}:${generation}`}
                resource={query.data}
                reload={() => void reload()}
              />
            ),
          },
        ]}
      />
    </Page>
  );
}
function AgentSettings({
  resource,
  reload,
}: {
  resource: { value: Schema["Agent"]; etag?: string };
  reload: () => void;
}) {
  const [snapshot] = useState(resource);
  const { value: agent, etag } = snapshot,
    client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    idempotency = useIdempotency();
  const [name, setName] = useState(agent.name),
    [description, setDescription] = useState(agent.description ?? ""),
    [environment, setEnvironment] = useState(
      agent.default_environment_template_id ?? "none",
    );
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
            params: {
              path: { workspace_id: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const save = useMutation({
    mutationFn: async () => {
      if (!etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      await client.http.PATCH("/api/v1/agents/{agent_id}", {
        params: { path: { agent_id: agent.id }, header: { "If-Match": etag } },
        headers: workspaceHeaders(workspace.id),
        body: {
          name,
          description: description || null,
          default_environment_template_id:
            environment === "none" ? null : environment,
        },
      });
    },
    onSuccess: reload,
  });
  const action = async (
    action: "enable" | "disable" | "archive" | "unarchive",
  ) => {
    if (!etag)
      throw new Error(
        t("Version information is unavailable. Reload this page."),
      );
    await client.http.POST("/api/v1/agents/{agent_id}/{action}", {
      params: {
        path: { agent_id: agent.id, action },
        header: {
          ...commandHeaders(
            workspace.id,
            idempotency.forBody({ action, etag }),
          ),
          "If-Match": etag,
        },
      },
    });
    idempotency.reset();
    reload();
  };
  return (
    <div className={styles.stack}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="fieldset-reset" disabled={!can("agent.update")}>
          <div className={styles.stack}>
            <Input
              label={t("Name")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
            <Input
              label={t("Description")}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <SelectField
              label={t("Default environment")}
              placeholder={t("Select environment")}
              value={environment}
              onValueChange={setEnvironment}
              options={[
                { value: "none", label: t("No default environment") },
                ...(templates.data?.map((item) => ({
                  value: item.id,
                  label: item.name,
                })) ?? []),
              ]}
            />
          </div>
          <FormActions pending={save.isPending} />
        </fieldset>
        <ErrorNotice error={save.error} retry={reload} />
      </form>
      {(can("agent.lifecycle") || can("agent.duplicate")) && (
        <div className={styles.actions}>
          {can("agent.lifecycle") && (
            <>
              <Confirm
                title={t(agent.enabled ? "Disable agent" : "Enable agent")}
                description={t("This changes whether new runs can start.")}
                trigger={t(agent.enabled ? "Disable" : "Enable")}
                action={() => action(agent.enabled ? "disable" : "enable")}
              />
              <Confirm
                title={t(
                  agent.archived_at ? "Unarchive agent" : "Archive agent",
                )}
                description={t(
                  "Archived agents leave the default list. Their history remains available.",
                )}
                trigger={t(agent.archived_at ? "Unarchive" : "Archive")}
                danger={!agent.archived_at}
                action={() =>
                  action(agent.archived_at ? "unarchive" : "archive")
                }
              />
            </>
          )}
          {can("agent.duplicate") && (
            <Confirm
              title={t("Duplicate agent")}
              description={t("Create an independent agent from this version.")}
              trigger={
                <>
                  <Copy size={13} />
                  {t("Duplicate")}
                </>
              }
              action={async () => {
                const body = {
                  expected_version: agent.version,
                  name: `${agent.name} (${t("copy")})`,
                };
                const result = data(
                  await client.http.POST(
                    "/api/v1/agents/{agent_id}/duplicate",
                    {
                      params: {
                        path: { agent_id: agent.id },
                        header: commandHeaders(
                          workspace.id,
                          idempotency.forBody(body),
                        ),
                      },
                      body,
                    },
                  ),
                );
                idempotency.reset();
                void cache.invalidateQueries();
                navigate(`/workspaces/${workspace.id}/agents/${result.id}`);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
function AgentVersions({ agent }: { agent: Schema["Agent"] }) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    page = useCursor(),
    idempotency = useIdempotency();
  const [selected, setSelected] = useState<Schema["AgentRevision"]>();
  const query = useQuery({
    queryKey: ["agent-revisions", workspace.id, agent.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/agents/{agent_id}/revisions", {
          params: {
            path: { agent_id: agent.id },
            query: { cursor: page.cursor, limit: 20 },
          },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t("Versions are immutable. Restoring one creates a new version.")}
      </p>
      <Table
        items={query.data.items}
        columns={[
          {
            label: t("Version"),
            render: (item) => (
              <Button variant="ghost" onClick={() => setSelected(item)}>
                v{item.version}
              </Button>
            ),
          },
          { label: t("Model"), render: (item) => item.config.model.model_key },
          {
            label: t("Created"),
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Actions"),
            render: (item) =>
              can("agent.revision.create") &&
              item.id !== agent.current_revision_id && (
                <Confirm
                  title={t("Restore version")}
                  description={t(
                    "This creates a new current version using the selected configuration.",
                  )}
                  trigger={t("Restore")}
                  action={async () => {
                    const body = { expected_version: agent.version };
                    await client.http.POST(
                      "/api/v1/agents/{agent_id}/revisions/{revision_id}/restore",
                      {
                        params: {
                          path: { agent_id: agent.id, revision_id: item.id },
                          header: commandHeaders(
                            workspace.id,
                            idempotency.forBody({ ...body, revision: item.id }),
                          ),
                        },
                        body,
                      },
                    );
                    idempotency.reset();
                    await cache.invalidateQueries();
                  }}
                />
              ),
          },
        ]}
      />
      <Pagination page={page} next={query.data.next_cursor} />
      {selected && (
        <section>
          <h3>
            {t("Version")} {selected.version}
          </h3>
          <JsonView value={selected.config} />
        </section>
      )}
    </div>
  );
}
