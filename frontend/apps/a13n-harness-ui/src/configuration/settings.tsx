import { useContext, useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { SettingsSection, Skeleton } from "a13n-ui";
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
import { RuntimePage } from "./envd";
import { NotificationSettings } from "../shell/notifications";
import layout from "./settings.module.css";
import { ForgetEnvironment } from "./forget-environment";
import styles from "../shell/workbench.module.css";
import environmentStyles from "./environments-page.module.css";

const sections = [
  { path: "/settings", label: "General" },
  { path: "/settings/agents", label: "Agents" },
  { path: "/settings/models", label: "Models" },
  { path: "/settings/environments", label: "Environments" },
  { path: "/settings/accounts", label: "Accounts & API keys" },
  { path: "/settings/connections", label: "Connections" },
  { path: "/settings/resources", label: "Configuration files" },
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
              ? "/settings/connections"
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
        {location.pathname.startsWith("/projects") && (
          <Link className={styles.activeNav} to="/projects" aria-current="page">
            Projects
          </Link>
        )}
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
              className={`${active ? styles.activeNav : styles.navLink} ${path === "/settings/resources" ? layout.utilityLink : ""}`}
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
  const location = useLocation();
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
        <div className={layout.formWidth}>
          <SourceDocument
            key={root.relative_path}
            path={root.relative_path}
            title="General settings"
            embedded
          />
        </div>
      ) : (
        <Panel title="Set up your workspace">
          <p>Create your first agent and choose defaults to get started.</p>
          <Link to="/setup">Start setup</Link>
        </Panel>
      )}
      <details
        id="notifications"
        className={layout.disclosure}
        open={location.hash === "#notifications" || undefined}
      >
        <summary>
          Notifications<small>Task results and requests for your input</small>
        </summary>
        <NotificationSettings embedded />
      </details>
      <details className={layout.disclosure}>
        <summary>Setup & diagnostics</summary>
        {memory && (
          <SettingsSection title="Memory organization status">
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
        <Link to="/setup">Guided setup and environment checks</Link>
        {[
          ...(status.data?.app.capability_warnings ?? []),
          ...(status.data?.app.content_plugin_diagnostics ?? []),
        ].map((message) => (
          <p key={message}>{message}</p>
        ))}
        <p>Version {status.data?.version}</p>
        <InstallSettings />
      </details>
    </>
  );
}

export function AgentsPage() {
  return (
    <>
      <SourcesPage
        kinds={["agent"]}
        title="Agents"
        description="Configure instructions, models and tools for each agent."
      />
      <details className={layout.disclosure}>
        <summary>Available capabilities</summary>
        <InstalledCatalog kind="capability" />
      </details>
    </>
  );
}

export function ConnectionsPage() {
  const sources = useSources();
  const root = sources.data?.sources.find(
    (item) => item.resource_kind === "root",
  );
  return (
    <>
      <PageHeader
        title="Connections"
        description="Tools and services your agents can use."
      />
      <SourcesPage
        kinds={["mcp_server"]}
        headingLevel={2}
        title="MCP servers"
      />
      <SourcesPage
        kinds={["harness_plugin"]}
        headingLevel={2}
        title="Agent plugins"
      />
      {root && (
        <details className={layout.disclosure}>
          <summary>Conversation defaults</summary>
          <SourceDocument
            path={root.relative_path}
            title="Default connections & extensions"
            embedded
            section="connections"
          />
        </details>
      )}
      <details className={layout.disclosure}>
        <summary>Available plugins</summary>
        <InstalledCatalog kind="harness_plugin" />
      </details>
    </>
  );
}

export function EnvironmentsPage() {
  const selectors = useSelectors();
  const sources = useSources();
  const [params] = useSearchParams();
  const tab = params.get("tab") ?? "local";
  const selectedTab = ["local", "devices", "profiles", "runtime"].includes(tab)
    ? tab
    : "local";
  return (
    <div className={environmentStyles.page}>
      <PageHeader
        title="Environments"
        description="Choose where and how your agents work."
      />
      <nav className={layout.tabs} aria-label="Environment sections">
        {[
          ["local", "Local execution"],
          ["devices", "Devices"],
          ["profiles", "Profiles & extensions"],
          ["runtime", "Local runtime"],
        ].map(([value, label]) => (
          <Link
            key={value}
            to={`/settings/environments?tab=${value}`}
            aria-current={selectedTab === value ? "page" : undefined}
          >
            {label}
          </Link>
        ))}
      </nav>
      <ErrorNotice error={selectors.error || sources.error} />
      {selectedTab === "local" && (
        <>
          {sources.data?.sources
            .filter((item) => item.resource_kind === "root")
            .map((root) => (
              <SourceDocument
                key={root.relative_path}
                path={root.relative_path}
                title="Execution defaults"
                embedded
                section="execution"
              />
            ))}
          <p className={layout.hint}>
            Choose folders and project-specific defaults from each Project’s
            settings.
          </p>
        </>
      )}
      {selectedTab === "devices" && <DevicesSection />}
      {selectedTab === "profiles" && (
        <>
          <section
            className={styles.stack}
            aria-labelledby="local-execution-profiles"
          >
            <div className={environmentStyles.sectionHeader}>
              <h2 id="local-execution-profiles">Local execution profiles</h2>
              <NewResourceButton
                kind="environment_profile"
                label="Add local profile"
                variant="outline"
              />
            </div>
            <p>
              Control how agents run on this server: full control or a sandbox.
            </p>
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
                    <small>
                      {item.release_owned ? "Built in" : "Configured"}
                    </small>
                  </>
                );
                return source ? (
                  <div key={item.profile_id} className={styles.resourceRow}>
                    <Link
                      className={environmentStyles.profileLink}
                      to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                    >
                      {content}
                    </Link>
                    {!item.release_owned &&
                      source.resource_ids.length === 1 && (
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
          <details className={layout.disclosure}>
            <summary>Installed environment providers</summary>
            <p>Create a profile from an installed provider.</p>
            <InstalledCatalog kind="environment_provider" />
          </details>
          <details className={layout.disclosure}>
            <summary>Environment extensions</summary>
            <SourcesPage
              kinds={["environment_run_extension"]}
              headingLevel={2}
              title="Environment extensions"
              description="Optional extensions selected in project or global defaults."
            />
          </details>
        </>
      )}
      {selectedTab === "runtime" && <RuntimePage embedded />}
    </div>
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
