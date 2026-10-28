import { ModelIcon, ModelIdentity } from "../shell/model-identity";
import { reasoningModeLabel } from "./reasoning-mode-picker";
import { useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { applyThreadMutation } from "./thread-updates";
import { refreshThread } from "./refresh";
import { refreshActivity } from "./activity-updates";
import { Button, Checkbox } from "a13n-ui";
import {
  SettingsChoice as ChoiceField,
  SettingsRow,
  SettingsSection,
} from "../shell/settings";
import layout from "../shell/workbench.module.css";
import { DebugData } from "../shell/debug";
import { useProjects, useSelectors, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import {
  EnvironmentsEditor,
  EnvironmentsSummary,
  invalidEnvironments,
} from "../configuration/environments";
import styles from "./conversation.module.css";

function ConfigurationSummary({
  configuration,
}: {
  configuration: Schema<"ThreadConfiguration">;
}) {
  const { data: catalog } = useSelectors();
  const resourceNames = (
    ids: string[] | undefined,
    resources: { resource_id: string; name: string }[] | undefined,
  ) =>
    ids
      ?.map(
        (id) =>
          resources?.find((resource) => resource.resource_id === id)?.name ??
          id,
      )
      .join(", ") || "None";
  return (
    <dl className={styles.detailGrid}>
      <div>
        <dt>Agent</dt>
        <dd>
          {catalog?.agents?.find(
            (agent) => agent.agent_id === configuration.agent_source.id,
          )?.name ?? configuration.agent_source.id}
        </dd>
      </div>
      <div>
        <dt>Default model</dt>
        <dd>
          {configuration.default_model_id ? (
            <ModelIdentity
              model={catalog?.models?.find(
                (model) => model.model_id === configuration.default_model_id,
              )}
              name={
                catalog?.models?.find(
                  (model) => model.model_id === configuration.default_model_id,
                )?.name ?? configuration.default_model_id
              }
            />
          ) : (
            "Follow agent model"
          )}
        </dd>
      </div>
      <div className={styles.detailLongRow}>
        <dt>Environments</dt>
        <dd>
          <EnvironmentsSummary
            value={configuration}
            profiles={catalog?.environments}
          />
        </dd>
      </div>
      <div>
        <dt>Plugins</dt>
        <dd>
          {resourceNames(
            configuration.harness_plugin_ids,
            catalog?.harness_plugins,
          )}
        </dd>
      </div>
      <div>
        <dt>Run extensions</dt>
        <dd>
          {resourceNames(
            configuration.environment_run_extension_ids,
            catalog?.environment_run_extensions,
          )}
        </dd>
      </div>
      <div>
        <dt>MCP servers</dt>
        <dd>
          {resourceNames(configuration.mcp_server_ids, catalog?.mcp_servers)}
        </dd>
      </div>
    </dl>
  );
}
export function ConversationConfiguration({
  threadId,
  reconcile,
  readOnly = false,
  actionsContainer,
}: {
  threadId: string;
  readOnly?: boolean;
  actionsContainer?: HTMLElement | null;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const inspection = useQuery({
    queryKey: ["thread", threadId, "configuration"],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/configuration", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
  const [reviewed, setReviewed] =
    useState<Schema<"ProjectDefaultsPreview"> | null>(null);
  const [applyEnvironments, setApplyEnvironments] = useState(false);
  const preview = useMutation({
    mutationFn: (environmentsOnly: boolean) =>
      result(
        client.GET(
          environmentsOnly
            ? "/api/threads/{thread_id}/project-environments"
            : "/api/threads/{thread_id}/project-defaults",
          {
            params: { path: { thread_id: threadId } },
          },
        ),
      ),
    onSuccess: setReviewed,
  });
  const apply = useMutation({
    mutationFn: () =>
      result(
        client.POST(
          applyEnvironments
            ? "/api/threads/{thread_id}/project-environments"
            : "/api/threads/{thread_id}/project-defaults",
          {
            params: { path: { thread_id: threadId } },
            body: {
              expected_version: reviewed!.expected_version,
              defaults_digest: reviewed!.defaults_digest,
            },
          },
        ),
      ),
    onSuccess: () => {
      setReviewed(null);
      reconcile();
    },
    onError: reconcile,
  });
  const data = inspection.data;
  return (
    <div className={styles.form}>
      <ErrorNotice
        error={inspection.error || preview.error || apply.error}
        retry={() => void inspection.refetch()}
      />
      {inspection.isPending && <p role="status">Loading settings…</p>}
      {data && (
        <>
          {data.next_run.configuration.agent_source.kind === "live" ? (
            <section>
              <h3>Live executor</h3>
              <p>
                Live voice has its own workspace and tools. Choose its model,
                voice and file access in General settings.
              </p>
              <p>Next model: {data.next_model_id || "Not configured"}</p>
            </section>
          ) : readOnly ? (
            <section>
              <h3>Automatic organization</h3>
              <p>
                This chat can only use its memory tools. It has no execution
                environment, shared tools, plugins, MCP servers or subagents.
              </p>
              <p>Next model: {data.next_model_id || "Not configured"}</p>
            </section>
          ) : (
            <section>
              <p className={styles.settingsHint}>
                Changes apply to the next run, using the latest shared settings.
              </p>
              <DebugData
                label="Where these settings come from"
                value={{
                  provenance: data.next_run.provenance,
                  tool_search: data.next_tool_search,
                }}
              />
              {!readOnly && (
                <ThreadSelections
                  key={threadId}
                  threadId={threadId}
                  embedded
                  actionsContainer={actionsContainer}
                  configuration={data.next_run.configuration}
                />
              )}
            </section>
          )}
          {!readOnly && data.next_run.configuration.project_id && (
            <details className={layout.details}>
              <summary>Apply Project defaults</summary>
              <p>Preview changes before replacing this chat’s settings.</p>
              <div className={styles.detailActions}>
                <Button
                  variant="outline"
                  loading={preview.isPending}
                  onClick={() => {
                    setReviewed(null);
                    apply.reset();
                    setApplyEnvironments(false);
                    preview.mutate(false);
                  }}
                >
                  Preview defaults
                </Button>
                <Button
                  variant="outline"
                  loading={preview.isPending}
                  onClick={() => {
                    setReviewed(null);
                    apply.reset();
                    setApplyEnvironments(true);
                    preview.mutate(true);
                  }}
                >
                  Environments only
                </Button>
              </div>
              <p>
                Environment-only changes keep your Agent, model and connections.
              </p>
              {reviewed && (
                <div className={styles.form}>
                  <div className={styles.detailGrid}>
                    <section>
                      <h4>Before · version {reviewed.expected_version}</h4>
                      <ConfigurationSummary configuration={reviewed.current} />
                    </section>
                    <section>
                      <h4>After</h4>
                      <ConfigurationSummary
                        configuration={reviewed.replacement}
                      />
                    </section>
                  </div>
                  <Button
                    loading={apply.isPending}
                    disabled={
                      !Object.keys(reviewed.patch).length || apply.isError
                    }
                    onClick={() => apply.mutate()}
                  >
                    {applyEnvironments
                      ? "Apply environments"
                      : "Apply defaults"}
                  </Button>
                  {apply.isError && (
                    <p>
                      Refresh and review the preview before applying. Nothing
                      was retried.
                    </p>
                  )}
                </div>
              )}
            </details>
          )}
          <details className={layout.details}>
            <summary>
              {data.capture_source === "active_operation"
                ? "Current run settings"
                : "Saved run settings"}
            </summary>
            {data.captured ? (
              <>
                <dl className={styles.detailGrid}>
                  <div>
                    <dt>Agent</dt>
                    <dd>{data.captured.agent.name}</dd>
                  </div>
                  <div>
                    <dt>Model</dt>
                    <dd>{data.captured.agent.model_id || "Not captured"}</dd>
                  </div>
                  <div>
                    <dt>Thinking (requested)</dt>
                    <dd>
                      {data.captured.agent.thinking_summary ?? "Not captured"}
                    </dd>
                  </div>
                  <div>
                    <dt>Reasoning mode (requested)</dt>
                    <dd>
                      {reasoningModeLabel(data.captured.agent.reasoning_mode)}
                    </dd>
                  </div>
                  <div className={styles.detailLongRow}>
                    <dt>Captured environments</dt>
                    <dd>
                      <EnvironmentsSummary
                        value={{
                          ...data.captured,
                          local_roots: data.captured.project_roots,
                        }}
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>Capabilities</dt>
                    <dd>{data.captured.capability_ids.join(", ") || "None"}</dd>
                  </div>
                </dl>
                <DebugData label="Configuration used" value={data.captured} />
              </>
            ) : (
              <p>
                {data.capture_source === "active_operation"
                  ? "Starting the run. Its settings will appear shortly."
                  : "No captured configuration is available."}
              </p>
            )}
          </details>
        </>
      )}
    </div>
  );
}
export function ThreadSelections({
  threadId,
  configuration: current,
  embedded = false,
  actionsContainer,
}: {
  threadId: string;
  configuration: Schema<"ThreadConfiguration">;
  embedded?: boolean;
  actionsContainer?: HTMLElement | null;
}) {
  const transport = useTransport();
  const { client } = transport;
  const queries = useQueryClient();
  const selectors = useSelectors();
  const projects = useProjects();
  const [saved, setSaved] = useState<Schema<"ThreadConfiguration"> | null>(
    null,
  );
  const latest = saved && saved.version > current.version ? saved : current;
  const [base, setBase] = useState<Schema<"ThreadConfiguration"> | null>(null);
  const configuration = base ?? latest;
  const [patch, updatePatch] = useState<Schema<"ThreadConfigurationPatch">>({});
  const setPatch = (next: Schema<"ThreadConfigurationPatch">) => {
    setBase(configuration);
    updatePatch(next);
  };
  const stale = base !== null && base.version !== latest.version;
  const mutation = useMutation({
    mutationFn: () =>
      result(
        client.PATCH("/api/threads/{thread_id}/configuration", {
          params: { path: { thread_id: threadId } },
          body: { expected_version: configuration.version, patch },
        }),
      ),
    onSuccess: (updated) => {
      applyThreadMutation(queries, updated);
      setSaved({
        ...updated.configuration,
        agent_source:
          updated.configuration.agent_source.kind === "live"
            ? { kind: "live", id: "live" }
            : {
                ...updated.configuration.agent_source,
                kind: updated.configuration.agent_source.kind,
              },
      });
      updatePatch({});
      setBase(null);
    },
    onSettled: () => {
      refreshThread(queries, threadId, "configuration");
      refreshActivity(queries, transport, threadId);
    },
  });
  const actions = (
    <div className={styles.settingsActions}>
      {!!Object.keys(patch).length && (
        <Button
          variant="ghost"
          aria-label="Discard selection edits"
          disabled={mutation.isPending}
          onClick={() => {
            updatePatch({});
            setBase(null);
            mutation.reset();
          }}
        >
          Discard
        </Button>
      )}

      {(!embedded || !!Object.keys(patch).length) && (
        <Button
          loading={mutation.isPending}
          disabled={
            !Object.keys(patch).length ||
            mutation.isError ||
            stale ||
            invalidEnvironments({ ...configuration, ...patch })
          }
          onClick={() => mutation.mutate()}
        >
          Save for next run
        </Button>
      )}
    </div>
  );
  const fields = (
    <fieldset className={styles.settingsFields} disabled={mutation.isPending}>
      <SettingsSection title="Agent & model">
        <ChoiceField
          label="Project"
          value={
            patch.project_id === undefined
              ? (configuration.project_id ?? "")
              : (patch.project_id ?? "")
          }
          onValueChange={(value) =>
            setPatch({ ...patch, project_id: value || null })
          }
          options={[
            { value: "", label: "Without a project" },
            ...(projects.data ?? []).map((project) => ({
              value: project.project_id,
              label: project.name,
            })),
          ]}
        />
        <ChoiceField
          label="Agent"
          value={patch.agent_id ?? configuration.agent_source.id}
          onValueChange={(agent_id) => setPatch({ ...patch, agent_id })}
          options={(selectors.data?.agents ?? []).map((agent) => ({
            value: agent.agent_id,
            label: agent.name,
          }))}
        />
        <ChoiceField
          label="Default model"
          value={
            patch.default_model_id === undefined
              ? (configuration.default_model_id ?? "")
              : (patch.default_model_id ?? "")
          }
          onValueChange={(value) =>
            setPatch({ ...patch, default_model_id: value || null })
          }
          options={[
            { value: "", label: "Follow Agent model" },
            ...(selectors.data?.models ?? []).map((model) => ({
              value: model.model_id,
              label: model.name,
              icon: <ModelIcon model={model} />,
            })),
          ]}
        />
      </SettingsSection>
      {patch.project_id !== undefined && (
        <p className={styles.settingsHint}>
          Moving a chat to a Project keeps its settings.
        </p>
      )}
      <SettingsSection title="Environments">
        <div className={layout.fieldGroup}>
          <EnvironmentsEditor
            value={{ ...configuration, ...patch }}
            profiles={selectors.data?.environments}
            inheritedProfile={configuration.environment_profile_id}
            inheritLabel="Keep chat local mode"
            onChange={(environments) => setPatch({ ...patch, ...environments })}
          />
        </div>
      </SettingsSection>
      <section className={styles.connectionSelections}>
        <SettingsSection title="Connections">
          {(
            [
              {
                key: "harness_plugin_ids",
                label: "Plugins",
                options: selectors.data?.harness_plugins,
              },
              {
                key: "environment_run_extension_ids",
                label: "Run extensions",
                options: selectors.data?.environment_run_extensions,
              },
              {
                key: "mcp_server_ids",
                label: "MCP servers",
                options: selectors.data?.mcp_servers,
              },
            ] as const
          ).map(({ key, label, options }) => (
            <div key={key}>
              <SettingsRow label={label}>
                <span className={layout.hint}>
                  {options?.length
                    ? `${(patch[key] ?? configuration[key] ?? []).length} selected`
                    : "None configured"}
                </span>
              </SettingsRow>
              {options?.map((option) => {
                const selected = patch[key] ?? configuration[key] ?? [];
                return (
                  <SettingsRow
                    key={option.resource_id}
                    label={option.name}
                    controlId={`thread-${threadId}-${key}-${option.resource_id}`}
                  >
                    <Checkbox
                      id={`thread-${threadId}-${key}-${option.resource_id}`}
                      checked={selected.includes(option.resource_id)}
                      disabled={mutation.isPending}
                      onCheckedChange={(checked) =>
                        setPatch({
                          ...patch,
                          [key]: checked
                            ? [...selected, option.resource_id]
                            : selected.filter(
                                (id) => id !== option.resource_id,
                              ),
                        })
                      }
                    />
                  </SettingsRow>
                );
              })}
            </div>
          ))}
        </SettingsSection>
        <Link className={layout.hint} to="/settings/connections">
          Manage connections
        </Link>
      </section>
      <ErrorNotice
        error={mutation.error || selectors.error || projects.error}
      />
      {(stale || mutation.isError) && (
        <div className={styles.warning}>
          <p>
            Your edits are based on version {configuration.version}. Review the
            latest settings below, then keep your edits to apply them to that
            version. Nothing was retried.
          </p>
          <h4>Latest selections · version {latest.version}</h4>
          <ConfigurationSummary configuration={latest} />
          <Button
            variant="outline"
            onClick={() => {
              setBase(latest);
              mutation.reset();
            }}
          >
            Keep my edits
          </Button>
        </div>
      )}
      {actionsContainer ? createPortal(actions, actionsContainer) : actions}
    </fieldset>
  );
  return embedded ? (
    fields
  ) : (
    <details className={layout.details}>
      <summary>Change settings for the next run</summary>
      {fields}
    </details>
  );
}
