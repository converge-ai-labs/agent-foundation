import { useContext, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField, SettingsSection, Skeleton } from "a13n-ui";
import {
  useSelectors,
  useSources,
  useStatus,
  useTransport,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import {
  SourceDocument,
  SourcesPage,
  NewResourceButton,
  DraftLinks,
  DraftContext,
} from "./sources";
import { readDocument, type ResourceKind } from "./documents";
import { InstallSettings } from "../shell/install";
import { DevicesSection } from "./devices";
import { ForgetEnvironment } from "./forget-environment";
import styles from "../shell/workbench.module.css";

const sections = [
  { path: "/settings", label: "General" },
  { path: "/settings/notifications", label: "Notifications" },
  { path: "/settings/agents", label: "Agents" },
  { path: "/settings/models", label: "Models" },
  { path: "/settings/capabilities", label: "Capabilities" },
  { path: "/settings/environments", label: "Environments" },
  { path: "/projects", label: "Projects" },
  { path: "/settings/accounts", label: "Accounts & API keys" },
  { path: "/settings/connections", label: "MCP connections" },
  { path: "/settings/resources", label: "Advanced" },
];
export function SettingsLayout() {
  const location = useLocation();
  const sources = useSources();
  const drafts = useContext(DraftContext);
  const path = new URLSearchParams(location.search).get("path") ?? "";
  const source = sources.data?.sources.find(
    (item) => item.relative_path === path,
  );
  const sourceKind =
    source?.resource_kind ??
    readDocument(drafts.get(path)?.content ?? "")?.get("kind");
  const sourceSection =
    sourceKind === "root"
      ? "/settings"
      : sourceKind === "project"
        ? "/projects"
        : sourceKind === "model"
          ? "/settings/models"
          : sourceKind === "agent"
            ? "/settings/agents"
            : sourceKind === "harness_plugin"
              ? "/settings/capabilities"
              : sourceKind === "environment_profile" ||
                  sourceKind === "device" ||
                  sourceKind === "environment_run_extension"
                ? "/settings/environments"
                : sourceKind === "mcp_server"
                  ? "/settings/connections"
                  : "/settings/resources";
  return (
    <div className={styles.settings}>
      <nav
        className={`${styles.settingsNav} a13n-scrollbar`}
        aria-label="Settings sections"
      >
        <h2>Settings</h2>
        {sections.map(({ path, label }) => {
          const active =
            location.pathname === "/settings/source"
              ? sourceSection === path
              : location.pathname === path ||
                (path !== "/settings" &&
                  location.pathname.startsWith(`${path}/`));
          return (
            <Link
              key={path}
              to={path}
              aria-current={active ? "page" : undefined}
              className={active ? styles.activeNav : styles.navLink}
            >
              {label}
            </Link>
          );
        })}
      </nav>
      <div className={`${styles.settingsContent} a13n-scrollbar`}>
        <Outlet />
      </div>
    </div>
  );
}

export function GeneralSettings() {
  const sources = useSources();
  const status = useStatus(5000);
  const memory = status.data?.app.memory_organization;
  const memoryAvailability = {
    ready: "Ready when changed files are eligible",
    disabled: "Disabled",
    model_not_configured:
      "Choose an organization model to enable background requests",
    webui_only: "Available in WebUI only",
    configuration_unavailable: "Configuration unavailable",
  };
  const root = sources.data?.sources.find(
    (item) => item.resource_kind === "root",
  );
  return (
    <>
      <ErrorNotice error={sources.error} />
      {sources.isPending ? (
        <Skeleton className="h-80 w-full" />
      ) : root ? (
        <SourceDocument
          key={root.relative_path}
          path={root.relative_path}
          title="General settings"
          embedded
        />
      ) : (
        <Panel title="Set up your workspace">
          <p>Create your first agent and choose defaults to get started.</p>
          <Link to="/setup">Start setup</Link>
        </Panel>
      )}
      {memory && (
        <SettingsSection
          title="Memory organization status"
          description="This server process only. Internal maintenance does not create conversations."
        >
          <p role="status" className={styles.fieldGroup}>
            {(memory.active_scopes?.length ?? 0) > 0
              ? `Organizing: ${memory.active_scopes?.join(", ")}`
              : memoryAvailability[memory.availability]}
            {memory.last_outcome &&
              ` · Last outcome: ${memory.last_outcome.replaceAll("_", " ")}`}
            {` · ${memory.attempts ?? 0} attempts · ${memory.requests ?? 0} model requests`}
            {` · ${memory.input_tokens ?? 0} input / ${memory.output_tokens ?? 0} output tokens`}
          </p>
        </SettingsSection>
      )}
      <InstallSettings />
      <details className={styles.details}>
        <summary>Setup & diagnostics</summary>
        <Link to="/setup">Guided setup and environment checks</Link>
        {[
          ...(status.data?.app.capability_warnings ?? []),
          ...(status.data?.app.content_plugin_diagnostics ?? []),
        ].map((message) => (
          <p key={message}>{message}</p>
        ))}
        <p>Version {status.data?.version}</p>
      </details>
    </>
  );
}

export function CapabilitiesPage() {
  const selectors = useSelectors();
  const sources = useSources();
  const status = useStatus();
  const [agentId, setAgentId] = useState("");
  const selected = agentId || selectors.data?.agents[0]?.agent_id || "";
  const source = sources.data?.sources.find(
    (item) =>
      item.resource_kind === "agent" && item.resource_ids.includes(selected),
  );
  return (
    <>
      <PageHeader
        title="Capabilities"
        description="Browse installed capabilities. Choose what each agent uses in its settings."
      />
      {status.data?.app.capability_warnings?.map((warning) => (
        <p role="status" key={warning}>
          {warning}
        </p>
      ))}
      <section
        className={styles.stack}
        aria-labelledby="installed-capabilities"
      >
        <h2 id="installed-capabilities">Installed capabilities</h2>
        <InstalledCatalog kind="capability" />
      </section>
      <section className={styles.stack} aria-labelledby="agent-capabilities">
        <h2 id="agent-capabilities">Agent capabilities</h2>
        <ErrorNotice error={selectors.error || sources.error} />
        {selectors.isPending || sources.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : !!selectors.data?.agents.length ? (
          <>
            <ChoiceField
              label="Agent"
              value={selected}
              onValueChange={setAgentId}
              options={selectors.data.agents.map((item) => ({
                value: item.agent_id,
                label: item.name,
              }))}
            />
            {source ? (
              <Link
                to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
              >
                {source.writable
                  ? "Edit agent capabilities"
                  : "View agent configuration"}
              </Link>
            ) : (
              <p>This agent has no configuration file available here.</p>
            )}
          </>
        ) : selectors.data ? (
          <Panel title="Create an agent to use capabilities">
            <NewResourceButton kind="agent" />
          </Panel>
        ) : null}
      </section>
      <SourcesPage
        kinds={["harness_plugin"]}
        headingLevel={2}
        title="Reusable agent plugins"
        description="Separate from capabilities: configure plugin instances here, then select them in agent or project defaults."
      />
      <details className={styles.details}>
        <summary>Available agent plugins</summary>
        <InstalledCatalog kind="harness_plugin" />
      </details>
    </>
  );
}

export function EnvironmentsPage() {
  const selectors = useSelectors();
  const sources = useSources();
  return (
    <>
      <PageHeader
        title="Environments"
        description="Manage where agents can work."
      />
      <p>
        Choose working directories and defaults in{" "}
        <Link to="/projects">Projects</Link>, or select environments for an
        individual conversation in its settings.
      </p>
      <ErrorNotice error={selectors.error || sources.error} />
      <DevicesSection />
      <section
        className={styles.stack}
        aria-labelledby="local-execution-profiles"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="local-execution-profiles">Local execution profiles</h2>
          <NewResourceButton
            kind="environment_profile"
            label="Add local profile"
            variant="outline"
          />
        </div>
        <p>Control how agents run on this server: full control or a sandbox.</p>
        <DraftLinks kinds={["environment_profile"]} />
        {selectors.isPending && <Skeleton className="h-32 w-full" />}
        <div className={styles.resourceList}>
          {selectors.data?.environments.map((item) => {
            const source = sources.data?.sources.find(
              (source) =>
                source.resource_kind === "environment_profile" &&
                source.resource_ids.includes(item.profile_id),
            );
            const content = (
              <>
                <div>
                  <strong>{item.name}</strong>
                  <small>{item.description}</small>
                </div>
                <span>
                  {item.mode === "full-control"
                    ? "Full control"
                    : item.mode === "sandbox"
                      ? "Sandbox"
                      : item.provider_key}
                </span>
                <small>{item.release_owned ? "Built in" : "Configured"}</small>
              </>
            );
            return source ? (
              <div key={item.profile_id} className={styles.resourceRow}>
                <Link
                  className="min-w-0 flex-1"
                  to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                >
                  {content}
                </Link>
                {!item.release_owned && source.resource_ids.length === 1 && (
                  <ForgetEnvironment
                    path={source.relative_path}
                    name={item.name}
                    resourceId={item.profile_id}
                  />
                )}
              </div>
            ) : (
              <div key={item.profile_id} className={styles.resourceRow}>
                {content}
              </div>
            );
          })}
        </div>
      </section>
      <details className={styles.details}>
        <summary>Installed environment providers</summary>
        <p>
          Configure an installed provider to create a profile. This does not
          install packages or verify that the environment is ready.
        </p>
        <InstalledCatalog kind="environment_provider" />
      </details>
      <details className={styles.details}>
        <summary>Environment extensions</summary>
        <SourcesPage
          kinds={["environment_run_extension"]}
          headingLevel={2}
          title="Environment extensions"
          description="Optional extensions selected in project or global defaults."
        />
      </details>
    </>
  );
}

export function InstalledCatalog({
  kind,
}: {
  kind: Schema<"CatalogReference">["kind"];
}) {
  const { client } = useTransport();
  const [search, setSearch] = useState("");
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  const items = catalog.data?.filter(
    (item) =>
      item.kind === kind &&
      `${item.key} ${item.distribution_name ?? ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const resource: ResourceKind | undefined =
    kind === "environment_provider"
      ? "environment_profile"
      : kind === "harness_plugin"
        ? "harness_plugin"
        : undefined;
  return (
    <div className={styles.stack}>
      <ErrorNotice error={catalog.error} retry={() => void catalog.refetch()} />
      <div className={styles.search}>
        <TextField
          type="search"
          label="Search installed plugins"
          value={search}
          onChange={setSearch}
        />
      </div>
      {catalog.isPending && <Skeleton className="h-32 w-full" />}
      <div className={styles.resourceList}>
        {items?.map((item) => (
          <div className={styles.resourceRow} key={`${item.kind}:${item.key}`}>
            <div>
              <strong>{item.key}</strong>
              <small>
                {item.distribution_name ??
                  (item.source === "pydantic" ? "Built-in" : item.source)}
                {item.distribution_version
                  ? ` · ${item.distribution_version}`
                  : ""}
              </small>
            </div>
            {!item.configurable ? (
              <span>Unavailable to configure</span>
            ) : resource ? (
              <NewResourceButton
                kind={resource}
                label="Configure"
                variant="outline"
                initial={{
                  [kind === "environment_provider"
                    ? "provider_key"
                    : "plugin_key"]: item.key,
                }}
              />
            ) : (
              <span>Available to agents</span>
            )}
          </div>
        ))}
      </div>
      {items?.length === 0 && <p>No matching installed plugins.</p>}
    </div>
  );
}
