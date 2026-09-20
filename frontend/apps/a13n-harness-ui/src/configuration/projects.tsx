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
import { DraftLinks, NewResourceButton, SourceDocument } from "./sources";

export function ProjectsPage() {
  const projects = useProjects();
  const [search, setSearch] = useState("");
  return (
    <>
      <PageHeader
        title="Projects"
        actions={<NewResourceButton kind="project" label="Add project" />}
      />
      <ErrorNotice
        error={projects.error}
        retry={() => void projects.refetch()}
      />
      <div className={styles.search}>
        <TextField
          type="search"
          label="Find projects"
          value={search}
          onChange={setSearch}
        />
      </div>
      <DraftLinks kinds={["project"]} search={search} />
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
                <span key={root}>{root}</span>
              ))}
              <span>Project settings</span>
            </Link>
          ))}
      </div>
      {projects.data?.length === 0 && (
        <Panel title="Work with or without a Project">
          <p>
            Projects are optional. Add one to group conversations around a
            server folder and choose their default agent and environment.
          </p>
          <NewResourceButton kind="project" label="Add your first project" />
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
      {source && (
        <SourceDocument
          key={source.relative_path}
          path={source.relative_path}
          title={project?.name ?? "Project settings"}
          embedded
        />
      )}
      <ErrorNotice error={projects.error || sources.error} />
      {projects.isPending ? (
        <p>Loading Project…</p>
      ) : !project ? (
        <Panel title="Project unavailable">
          <p>
            This project is not in your saved configuration. It may have been
            removed or renamed.
          </p>
          <Link to="/projects">All Projects</Link>
        </Panel>
      ) : (
        <>
          <p>
            Defaults apply to new conversations. Changing project folders also
            affects future runs in existing conversations.
          </p>
          <details className={styles.details}>
            <summary>Preview saved defaults & check environment</summary>
            <ReadinessPreview
              projectId={project.project_id}
              projectPath={project.roots[0]}
            />
          </details>
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
    <Panel title="New conversation settings">
      <div className={styles.stack}>
        <ErrorNotice error={selectors.error || preview.error} />
        <div className={styles.formGrid}>
          <ChoiceField
            label="Agent"
            value={agent}
            onValueChange={setAgent}
            options={[
              { value: "default", label: "Use default" },
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
              { value: "default", label: "Use default" },
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
          Preview settings
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
          Accounts & API keys separately. Unsaved Project edits are not included
          in this preview.
        </p>
      </div>
    </Panel>
  );
}
