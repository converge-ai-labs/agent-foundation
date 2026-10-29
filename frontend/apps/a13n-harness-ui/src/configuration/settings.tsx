import { SettingsRow, SettingsSection } from "../shell/settings";
import { capabilityLabel } from "./capability-label";
import { useContext, useState } from "react";
import { Link, Outlet, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Skeleton, Switch } from "a13n-ui";
import { debugShortcutLabel, useDebug } from "../shell/debug";
import {
  useSelectors,
  useSources,
  useStatus,
  useTransport,
} from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, SearchField } from "../shell/ui";
import {
  SourceDocument,
  SourcesPage,
  NewResourceButton,
  DraftLinks,
} from "./sources";
import { DraftContext } from "./drafts";
import { readDocument, type ResourceKind } from "./documents";
import { InstallSettings } from "../shell/install";
import { DevicesSection } from "./devices";
import { RuntimePage } from "./envd";
import { NotificationSettings } from "../shell/notifications";
import layout from "./settings.module.css";
import { ForgetEnvironment } from "./forget-environment";
import styles from "../shell/workbench.module.css";
import environmentStyles from "./environments-page.module.css";

export function SettingsLayout() {
  return (
    <div className={`${styles.settingsContent} a13n-scrollbar`}>
      <Outlet />
    </div>
  );
}

function DebugModeSetting() {
  const debug = useDebug();
  return (
    <SettingsSection variant="grouped" title="Developer">
      <SettingsRow
        label="Debug mode"
        stackOnNarrow={false}
        controlId="debug-mode"
        description={
          <>
            Show identifiers, raw payloads, events and token details across the
            app. Toggle anywhere with{" "}
            <kbd className={styles.kbd}>{debugShortcutLabel()}</kbd>.
          </>
        }
      >
        <Switch
          id="debug-mode"
          checked={debug.enabled}
          onCheckedChange={(checked) => debug.setEnabled(checked)}
        />
      </SettingsRow>
    </SettingsSection>
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
        <div className={layout.formWidth}>
          <SourceDocument
            key={root.relative_path}
            path={root.relative_path}
            title="General"
            embedded
          />
        </div>
      ) : (
        <Panel title="Set up your workspace">
          <p>Add an agent to get started.</p>
          <Link to="/setup">Start setup</Link>
        </Panel>
      )}
      <section id="notifications">
        <NotificationSettings embedded />
      </section>
      <div className={layout.formWidth}>
        <DebugModeSetting />
      </div>
      <details className={layout.disclosure}>
        <summary>Setup and diagnostics</summary>
        {memory && (
          <SettingsSection variant="plain" title="Memory organization status">
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
        <summary>Installed capabilities</summary>
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
        title="Plugins"
      />
      <div className={layout.disclosures}>
        {root && (
          <details className={layout.disclosure}>
            <summary>
              <span>Chat defaults</span>
              <small>Connections and extensions for new chats</small>
            </summary>
            <SourceDocument
              path={root.relative_path}
              title={null}
              embedded
              section="connections"
            />
          </details>
        )}
        <details className={layout.disclosure}>
          <summary>Installed plugins</summary>
          <InstalledCatalog kind="harness_plugin" />
        </details>
      </div>
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
      <nav
        className={`${layout.tabs} a13n-scrollbar`}
        aria-label="Environment sections"
      >
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
            Choose folders and defaults in each project's settings.
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
              <h2 id="local-execution-profiles">Local profiles</h2>
              <NewResourceButton
                kind="environment_profile"
                label="Add local profile"
                variant="outline"
              />
            </div>
            <p className={layout.hint}>
              Choose full access or a sandbox for local work.
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
            <summary>
              <span>Environment providers</span>
              <small>Create a profile from an installed provider</small>
            </summary>
            <InstalledCatalog kind="environment_provider" />
          </details>
          <SourcesPage
            kinds={["environment_run_extension"]}
            headingLevel={2}
            title="Run extensions"
            description="Extra features for runs. Choose them in project or global defaults."
          />
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
  const available = catalog.data?.filter((item) => item.kind === kind);
  const catalogLabel =
    kind === "capability"
      ? "capabilities"
      : kind === "environment_provider"
        ? "environment providers"
        : "plugins";
  const items = available?.filter((item) =>
    `${item.key} ${item.kind === "capability" ? capabilityLabel(item.key) : ""} ${item.distribution_name ?? ""}`
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
      {!!available?.length && (
        <SearchField
          label={`Search installed ${catalogLabel}`}
          value={search}
          onChange={setSearch}
        />
      )}
      {catalog.isPending && <Skeleton className="h-32 w-full" />}
      <div className={styles.resourceList}>
        {items?.map((item) => (
          <div className={styles.resourceRow} key={`${item.kind}:${item.key}`}>
            <div>
              <strong>
                {item.kind === "capability"
                  ? capabilityLabel(item.key)
                  : item.key}
              </strong>
              <small>
                {item.distribution_name ??
                  (item.source === "pydantic" ? "Built-in" : item.source)}
                {item.distribution_version
                  ? ` · ${item.distribution_version}`
                  : ""}
              </small>
            </div>
            {!item.configurable ? (
              <span className={layout.hint}>No setup form</span>
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
              <span className={layout.hint}>Available to agents</span>
            )}
          </div>
        ))}
      </div>
      {items?.length === 0 && (
        <p className={layout.hint}>
          {available?.length
            ? `No matching installed ${catalogLabel}.`
            : `No installed ${catalogLabel}.`}
        </p>
      )}
    </div>
  );
}
