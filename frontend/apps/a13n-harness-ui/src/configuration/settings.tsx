import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField, Skeleton } from "a13n-ui";
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
} from "./sources";
import type { ResourceKind } from "./documents";
import { InstallSettings } from "../shell/install";
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
  const source = sources.data?.sources.find(
    (item) =>
      item.relative_path === new URLSearchParams(location.search).get("path"),
  );
  const sourceSection =
    source?.resource_kind === "root"
      ? "/settings"
      : source?.resource_kind === "project"
        ? "/projects"
        : source?.resource_kind === "model"
          ? "/settings/models"
          : source?.resource_kind === "agent"
            ? "/settings/agents"
            : source?.resource_kind === "environment_profile"
              ? "/settings/environments"
              : source?.resource_kind === "mcp_server"
                ? "/settings/connections"
                : "/settings/resources";
  return (
    <div className={styles.settings}>
      <nav
        className={`${styles.settingsNav} a13n-scrollbar`}
        aria-label="Settings sections"
      >
        <h2>Settings</h2>
        {sections.map(({ path, label }) => (
          <NavLink
            key={path}
            to={path}
            end={path === "/settings"}
            className={({ isActive }) =>
              isActive ||
              (location.pathname === "/settings/source" &&
                sourceSection === path)
                ? styles.activeNav
                : styles.navLink
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <div className={`${styles.settingsContent} a13n-scrollbar`}>
        <Outlet />
      </div>
    </div>
  );
}

export function GeneralSettings() {
  const sources = useSources();
  const status = useStatus();
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
  const agent = selectors.data?.agents.find(
    (item) => item.agent_id === selected,
  );
  const source = sources.data?.sources.find(
    (item) =>
      item.resource_kind === "agent" && item.resource_ids.includes(selected),
  );
  return (
    <>
      <PageHeader
        title="Capabilities"
        description="Choose which installed capability plugins an agent uses. Your changes take effect after saving."
      />
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
            <SourceDocument
              key={source.relative_path}
              path={source.relative_path}
              title={agent?.name}
              capabilitiesOnly
              embedded
            />
          ) : (
            <p>This agent has no editable configuration file.</p>
          )}
        </>
      ) : (
        <Panel title="Create an agent first">
          <p>Capabilities belong to an agent, not to the workspace globally.</p>
          <NewResourceButton kind="agent" />
        </Panel>
      )}
      {status.data?.app.capability_warnings?.map((warning) => (
        <p role="status" key={warning}>
          {warning}
        </p>
      ))}
      <details className={styles.details}>
        <summary>Installed capability plugins</summary>
        <InstalledCatalog kind="capability" />
      </details>
      <SourcesPage
        kinds={["harness_plugin"]}
        headingLevel={2}
        title="Agent plugins"
        description="Configure reusable agent plugins here, then select them in agent or project defaults."
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
        description="Choose where agents work. Configure an environment here, then select it in General or Project settings."
        actions={
          <NewResourceButton
            kind="environment_profile"
            label="Add environment"
          />
        }
      />
      <ErrorNotice error={selectors.error || sources.error} />
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
            <Link
              key={item.profile_id}
              className={styles.resourceRow}
              to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
            >
              {content}
            </Link>
          ) : (
            <div key={item.profile_id} className={styles.resourceRow}>
              {content}
            </div>
          );
        })}
      </div>
      <section className={styles.stack}>
        <h2>Installed environment providers</h2>
        <p>
          Providers are installed on the server. Add an environment to configure
          an available provider and its adapter.
        </p>
        <InstalledCatalog kind="environment_provider" />
      </section>
      <details className={styles.details}>
        <summary>Environment extensions</summary>
        <SourcesPage
          kinds={["environment_run_extension"]}
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
                {item.distribution_name ?? item.source}
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
