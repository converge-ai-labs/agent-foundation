import {
  SettingsChoice as ChoiceField,
  SettingsSection,
} from "../shell/settings";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { Folder, CaretRight } from "@phosphor-icons/react";
import {
  useProjects,
  useSelectors,
  useSources,
  useTransport,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, SearchField } from "../shell/ui";
import { Preflight } from "../setup/setup";
import styles from "../shell/workbench.module.css";
import projectStyles from "./projects.module.css";
import collectionStyles from "./settings.module.css";
import { EnvironmentsSummary } from "./environments";
import { SelectionField } from "./selection";
import { DraftLinks, NewResourceButton, SourceDocument } from "./sources";

export function ProjectsPage() {
  const projects = useProjects();
  const [search, setSearch] = useState("");
  const filteredProjects = (projects.data ?? []).filter((project) =>
    `${project.name} ${project.roots.join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className={collectionStyles.collectionPage}>
      <PageHeader
        title="Projects"
        description="Keep related chats, folders and defaults together."
        actions={<NewResourceButton kind="project" label="Add project" />}
      />
      <div className={collectionStyles.collection}>
        <div className={collectionStyles.toolbar}>
          <SearchField
            label="Search projects"
            value={search}
            onChange={setSearch}
          />
        </div>
        <ErrorNotice
          error={projects.error}
          retry={() => void projects.refetch()}
        />
        <DraftLinks kinds={["project"]} search={search} />
        {projects.isPending && <p role="status">Loading projects…</p>}
        {!!filteredProjects.length && (
          <div className={projectStyles.list}>
            {filteredProjects.map((project) => (
              <Link
                className={projectStyles.project}
                key={project.project_id}
                to={`/projects/${encodeURIComponent(project.project_id)}`}
              >
                <Folder size={18} aria-hidden="true" />
                <div>
                  <strong>{project.name}</strong>
                  <span title={project.roots.join("\n")}>
                    {project.roots[0] || "No local directory"}
                  </span>
                  {(project.roots.length > 1 ||
                    !!project.defaults?.environment_bindings?.length) && (
                    <small>
                      {project.roots.length} local directories
                      {!!project.defaults?.environment_bindings?.length &&
                        ` · ${project.defaults.environment_bindings.length} remote environments`}
                    </small>
                  )}
                </div>
                <CaretRight size={14} aria-hidden="true" />
              </Link>
            ))}
          </div>
        )}
        {projects.data && !filteredProjects.length && (
          <p className={styles.emptyList}>
            {search
              ? "No projects match your search."
              : "No projects yet. Add one to group chats and share defaults."}
          </p>
        )}
      </div>
    </div>
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
            New chats use these defaults. Apply them from an existing chat's
            settings to update that chat.
          </p>
          <details className={styles.details}>
            <summary>Preview defaults and check readiness</summary>
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
    <div className={styles.stack}>
      <ErrorNotice error={selectors.error || preview.error} />
      <SettingsSection title="New chat settings">
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
          label="Local mode"
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
        {(
          [
            ["harness_plugin_ids", "Plugins", selectors.data?.harness_plugins],
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
      </SettingsSection>
      <Button loading={preview.isPending} onClick={() => preview.mutate(body)}>
        Preview settings
      </Button>
      {config && (
        <>
          <p>
            {current
              ? "Preview only. No chat or run was created."
              : "Settings changed. Refresh the preview."}
          </p>
          <EnvironmentsSummary
            value={config}
            profiles={selectors.data?.environments}
          />
          <p className="text-sm text-muted-foreground">
            Checks local execution only. Device connections aren't tested.
          </p>
          <dl className={styles.definitionList}>
            {Object.entries(preview.data!.provenance).map(([axis, origin]) => (
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
            ))}
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
        Checks saved settings only. It doesn't call a model or test credentials.
        Check those in Accounts & API keys.
      </p>
    </div>
  );
}
