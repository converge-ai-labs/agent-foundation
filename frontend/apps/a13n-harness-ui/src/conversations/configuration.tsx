import { useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useProjects, useSelectors, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";

function ConfigurationSummary({
  configuration,
}: {
  configuration: Schema<"ThreadConfiguration">;
}) {
  return (
    <dl className={styles.detailGrid}>
      <div>
        <dt>Agent</dt>
        <dd>{configuration.agent_source.id}</dd>
      </div>
      <div>
        <dt>Environment</dt>
        <dd>{configuration.environment_profile_id}</dd>
      </div>
      <div>
        <dt>Harness plugins</dt>
        <dd>{configuration.harness_plugin_ids?.join(", ") || "None"}</dd>
      </div>
      <div>
        <dt>Environment extensions</dt>
        <dd>
          {configuration.environment_run_extension_ids?.join(", ") || "None"}
        </dd>
      </div>
      <div>
        <dt>MCP servers</dt>
        <dd>{configuration.mcp_server_ids?.join(", ") || "None"}</dd>
      </div>
    </dl>
  );
}
export function ConversationConfiguration({
  threadId,
  reconcile,
}: {
  threadId: string;
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
  const preview = useMutation({
    mutationFn: () =>
      result(
        client.GET("/api/threads/{thread_id}/project-defaults", {
          params: { path: { thread_id: threadId } },
        }),
      ),
    onSuccess: setReviewed,
  });
  const apply = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/threads/{thread_id}/project-defaults", {
          params: { path: { thread_id: threadId } },
          body: {
            expected_version: reviewed!.expected_version,
            defaults_digest: reviewed!.defaults_digest,
          },
        }),
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
      {data && (
        <>
          <section>
            <h3>
              {data.capture_source === "active_operation"
                ? "Current Run capture"
                : "Last selected continuation capture"}
            </h3>
            {data.captured ? (
              <>
                <dl className={styles.detailGrid}>
                  <div>
                    <dt>Agent / Model</dt>
                    <dd>
                      {data.captured.agent.name} /{" "}
                      {data.captured.agent.model_id || "Not captured"}
                    </dd>
                  </div>
                  <div>
                    <dt>Environment</dt>
                    <dd>{data.captured.environment_profile_id}</dd>
                  </div>
                  <div>
                    <dt>Project roots on server</dt>
                    <dd>{data.captured.project_roots.join(", ") || "None"}</dd>
                  </div>
                  <div>
                    <dt>Capabilities</dt>
                    <dd>{data.captured.capability_ids.join(", ") || "None"}</dd>
                  </div>
                </dl>
                <details className={styles.activity}>
                  <summary>Inspect bounded captured configuration</summary>
                  <pre className={styles.code}>
                    {JSON.stringify(data.captured, null, 2)}
                  </pre>
                </details>
              </>
            ) : (
              <p>
                {data.capture_source === "active_operation"
                  ? "This operation is preparing. Its configuration has not been captured yet; the previous Run is not a substitute."
                  : "No captured configuration is available."}
              </p>
            )}
          </section>
          <section>
            <h3>Next Run selections</h3>
            <p>
              Changes here affect later Runs, not an already running Agent.
              Shared resource content is resolved again at the next Run.
            </p>
            <ConfigurationSummary configuration={data.next_run.configuration} />
            <p>Model: {data.next_model_id || "Not configured"}</p>
            <details className={styles.activity}>
              <summary>Selection origins and tool proxy</summary>
              <pre className={styles.code}>
                {JSON.stringify(
                  {
                    provenance: data.next_run.provenance,
                    tool_proxy: data.next_tool_proxy,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <ThreadSelections
              key={threadId}
              threadId={threadId}
              configuration={data.next_run.configuration}
              reconcile={reconcile}
            />
            <Link to="/settings/resources">Manage shared resources</Link>
          </section>
          {data.next_run.configuration.project_id && (
            <section>
              <h3>Apply Project defaults</h3>
              <p>
                Only explicitly configured Project axes are replaced.
                Unspecified selections stay unchanged.
              </p>
              <Button
                variant="outline"
                loading={preview.isPending}
                onClick={() => {
                  setReviewed(null);
                  apply.reset();
                  preview.mutate();
                }}
              >
                Preview changes
              </Button>
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
                    Apply reviewed defaults
                  </Button>
                  {apply.isError && (
                    <p>
                      Refresh the preview and review it again before applying.
                      No changes were retried.
                    </p>
                  )}
                </div>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
export function ThreadSelections({
  threadId,
  configuration: current,
  reconcile,
}: {
  threadId: string;
  configuration: Schema<"ThreadConfiguration">;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const selectors = useSelectors();
  const projects = useProjects();
  const [base, setBase] = useState<Schema<"ThreadConfiguration"> | null>(null);
  const configuration = base ?? current;
  const [patch, updatePatch] = useState<Schema<"ThreadConfigurationPatch">>({});
  const setPatch = (next: Schema<"ThreadConfigurationPatch">) => {
    setBase(configuration);
    updatePatch(next);
  };
  const stale = base !== null && base.version !== current.version;
  const mutation = useMutation({
    mutationFn: () =>
      result(
        client.PATCH("/api/threads/{thread_id}/configuration", {
          params: { path: { thread_id: threadId } },
          body: { expected_version: configuration.version, patch },
        }),
      ),
    onSuccess: () => {
      updatePatch({});
      setBase(null);
      reconcile();
    },
    onError: reconcile,
  });
  return (
    <details className={styles.activity}>
      <summary>Change next Run selections</summary>
      <fieldset
        className={`${styles.form} ${styles.responseInputs}`}
        disabled={mutation.isPending}
      >
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
          label="Environment"
          value={
            patch.environment_profile_id ?? configuration.environment_profile_id
          }
          onValueChange={(environment_profile_id) =>
            setPatch({ ...patch, environment_profile_id })
          }
          options={(selectors.data?.environments ?? []).map((environment) => ({
            value: environment.profile_id,
            label: environment.name,
          }))}
        />
        {(
          [
            {
              key: "harness_plugin_ids",
              label: "Harness plugins",
              options: selectors.data?.harness_plugins,
            },
            {
              key: "environment_run_extension_ids",
              label: "Environment extensions",
              options: selectors.data?.environment_run_extensions,
            },
            {
              key: "mcp_server_ids",
              label: "MCP servers",
              options: selectors.data?.mcp_servers,
            },
          ] as const
        ).map(({ key, label, options }) => (
          <fieldset key={key} className={styles.question}>
            <legend>{label}</legend>
            {options?.map((option) => {
              const selected = patch[key] ?? configuration[key] ?? [];
              return (
                <label className={styles.answerOption} key={option.resource_id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(option.resource_id)}
                    onChange={(event) =>
                      setPatch({
                        ...patch,
                        [key]: event.target.checked
                          ? [...selected, option.resource_id]
                          : selected.filter((id) => id !== option.resource_id),
                      })
                    }
                  />
                  {option.name}
                </label>
              );
            })}
            {!options?.length && <p>No configured resources.</p>}
          </fieldset>
        ))}
        <ErrorNotice
          error={mutation.error || selectors.error || projects.error}
        />
        {(stale || mutation.isError) && (
          <div className={styles.warning}>
            <p>
              Your edits are retained against version {configuration.version}.
              Review the latest selections below before applying your changes.
              No write was retried.
            </p>
            <h4>Latest selections · version {current.version}</h4>
            <ConfigurationSummary configuration={current} />
            <Button
              variant="outline"
              onClick={() => {
                setBase(current);
                mutation.reset();
              }}
            >
              Keep my edits against version {current.version}
            </Button>
          </div>
        )}
        <Button
          loading={mutation.isPending}
          disabled={!Object.keys(patch).length || mutation.isError || stale}
          onClick={() => mutation.mutate()}
        >
          Save next Run selections
        </Button>
        {!!Object.keys(patch).length && (
          <Button
            variant="ghost"
            disabled={mutation.isPending}
            onClick={() => {
              updatePatch({});
              setBase(null);
              mutation.reset();
            }}
          >
            Discard selection edits
          </Button>
        )}
      </fieldset>
    </details>
  );
}
