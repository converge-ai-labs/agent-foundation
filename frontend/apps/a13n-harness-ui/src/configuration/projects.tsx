import { useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { Folder } from "@phosphor-icons/react";
import {
  useProjects,
  useSelectors,
  useSources,
  useTransport,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import { Preflight } from "../setup/setup";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";

export function ProjectsPage() {
  const projects = useProjects();
  const [search, setSearch] = useState("");
  return (
    <>
      <PageHeader
        title="Projects"
        description="Named host directories with defaults for new threads. Existing threads keep their own selections."
        actions={
          <Button render={<Link to="/settings/resources" />}>
            Manage resources
          </Button>
        }
      />
      <ErrorNotice
        error={projects.error}
        retry={() => void projects.refetch()}
      />
      <TextField label="Find projects" value={search} onChange={setSearch} />
      {projects.isPending && <p role="status">Loading projects…</p>}
      <div className={styles.cardGrid}>
        {projects.data
          ?.filter((project) =>
            `${project.name} ${project.roots.join(" ")}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((project) => (
            <Link
              className={styles.projectCard}
              key={project.project_id}
              to={`/projects/${encodeURIComponent(project.project_id)}`}
            >
              <Folder size={24} />
              <strong>{project.name}</strong>
              <small>{project.project_id}</small>
              {project.roots.map((root) => (
                <code key={root}>{root}</code>
              ))}
              <span>Inspect defaults and readiness</span>
            </Link>
          ))}
      </div>
      {projects.data?.length === 0 && (
        <Panel title="Work with or without a Project">
          <p>
            A Project is optional. Create one in Resources to name existing host
            directories and choose defaults.
          </p>
          <Link to="/settings/resources">Create a Project resource</Link>
        </Panel>
      )}
    </>
  );
}
export function ProjectPage() {
  const { projectId } = useParams();
  const projects = useProjects();
  const sources = useSources();
  const project = projects.data?.find((item) => item.project_id === projectId);
  const source = sources.data?.sources.find((item) =>
    item.resource_ids.includes(projectId ?? ""),
  );
  return (
    <>
      <PageHeader
        title={project?.name ?? "Project"}
        description="Inspect accepted defaults and check the next thread configuration without starting a run."
        actions={
          source && (
            <Button
              variant="outline"
              render={
                <Link
                  to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                />
              }
            >
              Edit Project
            </Button>
          )
        }
      />
      <ErrorNotice error={projects.error || sources.error} />
      {projects.isPending ? (
        <p>Loading Project…</p>
      ) : !project ? (
        <Panel title="Project unavailable">
          <p>
            This Project is not in the accepted configuration. It may have been
            removed or renamed.
          </p>
          <Link to="/projects">All Projects</Link>
        </Panel>
      ) : (
        <>
          <Panel title="Host roots">
            {project.roots.map((root) => (
              <p key={root}>
                <code>{root}</code>
              </p>
            ))}
          </Panel>
          <ReadinessPreview
            projectId={project.project_id}
            projectPath={project.roots[0]}
          />
        </>
      )}
    </>
  );
}

export function ReadinessPreview({
  projectId,
  projectPath,
}: {
  projectId?: string;
  projectPath?: string;
}) {
  const { client } = useTransport();
  const selectors = useSelectors();
  const [agent, setAgent] = useState("default");
  const [environment, setEnvironment] = useState("default");
  const [lists, setLists] = useState<
    Pick<
      Schema<"NewThreadDefaults">,
      "harness_plugin_ids" | "environment_run_extension_ids" | "mcp_server_ids"
    >
  >({});
  const body: Schema<"NewThreadDefaults"> = {
    ...(projectId ? { project_id: projectId } : { project_id: null }),
    ...(agent === "default" ? {} : { agent_id: agent }),
    ...(environment === "default"
      ? {}
      : { environment_profile_id: environment }),
    ...lists,
  };
  const [inspected, setInspected] = useState("");
  const preview = useMutation({
    mutationFn: (body: Schema<"NewThreadDefaults">) =>
      result(client.POST("/api/threads/configuration-preview", { body })),
    onSuccess: (_, body) => setInspected(JSON.stringify(body)),
  });
  const config = preview.data?.configuration;
  const effectiveEnvironment = config?.environment_profile_id;
  const current = inspected === JSON.stringify(body);
  return (
    <Panel title="New thread readiness">
      <div className={styles.stack}>
        <ErrorNotice error={selectors.error || preview.error} />
        <div className={styles.formGrid}>
          <ChoiceField
            label="Agent"
            value={agent}
            onValueChange={setAgent}
            options={[
              { value: "default", label: "Default (inherit)" },
              ...(selectors.data?.agents.map((item) => ({
                value: item.agent_id,
                label: item.name,
              })) ?? []),
            ]}
          />
          <ChoiceField
            label="Environment"
            value={environment}
            onValueChange={setEnvironment}
            options={[
              { value: "default", label: "Default (inherit)" },
              ...(selectors.data?.environments.map((item) => ({
                value: item.profile_id,
                label: item.name,
              })) ?? []),
            ]}
          />
        </div>
        {(
          [
            [
              "harness_plugin_ids",
              "Harness plugins",
              selectors.data?.harness_plugins,
            ],
            [
              "environment_run_extension_ids",
              "Run extensions",
              selectors.data?.environment_run_extensions,
            ],
            ["mcp_server_ids", "MCP servers", selectors.data?.mcp_servers],
          ] as const
        ).map(([axis, label, options]) => (
          <SelectionField
            key={axis}
            label={label}
            value={lists[axis]}
            options={
              options?.map((option) => ({
                value: option.resource_id,
                label: option.name,
              })) ?? []
            }
            onChange={(value) =>
              setLists((previous) => ({ ...previous, [axis]: value }))
            }
          />
        ))}
        <Button
          loading={preview.isPending}
          onClick={() => preview.mutate(body)}
        >
          Inspect effective configuration
        </Button>
        {config && (
          <>
            <p>
              {current
                ? "Accepted configuration preview. No thread or run was created."
                : "Selections changed. Inspect again to refresh this preview."}
            </p>
            <dl className={styles.definitionList}>
              {Object.entries(preview.data!.provenance).map(
                ([axis, origin]) => (
                  <div key={axis}>
                    <dt>{axis.replaceAll("_", " ")}</dt>
                    <dd>
                      <code>
                        {JSON.stringify(config[axis as keyof typeof config]) ??
                          "Default"}
                      </code>
                      <small>From {origin}</small>
                    </dd>
                  </div>
                ),
              )}
            </dl>
            {current &&
              (effectiveEnvironment === "environment-native" ||
                effectiveEnvironment === "environment-sandbox") && (
                <Preflight
                  profile={effectiveEnvironment}
                  projectPath={projectPath}
                />
              )}
          </>
        )}
        <p>
          Readiness does not call a model or prove credentials are usable. Check
          Provider accounts separately. Unsaved Project edits are not included
          in this preview.
        </p>
      </div>
    </Panel>
  );
}
