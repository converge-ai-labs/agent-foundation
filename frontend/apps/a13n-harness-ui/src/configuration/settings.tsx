import { ConnectorsSection } from "../connectors/connections";
import { LanguageChoice } from "../i18n/language";
import { useTranslation } from "../i18n";
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
  const { t } = useTranslation();
  const debug = useDebug();
  return (
    <SettingsSection variant="grouped" title={t("Developer")}>
      <SettingsRow
        label={t("Debug mode")}
        stackOnNarrow={false}
        controlId="debug-mode"
        description={
          <>
            {t(
              "Show identifiers, raw payloads, events and token details across the app. Toggle anywhere with",
            )}{" "}
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
  const { t } = useTranslation();
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
      <PageHeader title={t("General")} />
      <SettingsSection>
        <LanguageChoice presentation="row" />
      </SettingsSection>
      <ErrorNotice error={sources.error} />
      {sources.isPending ? (
        <Skeleton className="h-80 w-full" />
      ) : root ? (
        <div className={layout.formWidth}>
          <SourceDocument
            key={root.relative_path}
            path={root.relative_path}
            title={null}
            embedded
          />
        </div>
      ) : (
        <Panel title={t("Set up your workspace")}>
          <p>{t("Add an agent to get started.")}</p>
          <Link to="/setup">{t("Start setup")}</Link>
        </Panel>
      )}
      <section id="notifications">
        <NotificationSettings embedded />
      </section>
      <div className={layout.formWidth}>
        <DebugModeSetting />
      </div>
      <details className={layout.disclosure}>
        <summary>{t("Setup and diagnostics")}</summary>
        {memory && (
          <SettingsSection
            variant="plain"
            title={t("Memory organization status")}
          >
            <p role="status" className={styles.fieldGroup}>
              {(memory.active_scopes?.length ?? 0) > 0
                ? t("Organizing: {{scopes}}", {
                    scopes: memory.active_scopes?.join(", "),
                  })
                : t(memoryAvailability[memory.availability])}
              {memory.last_outcome &&
                ` - ${t("Last outcome: {{outcome}}", { outcome: memory.last_outcome.replaceAll("_", " ") })}`}
              {` - ${t("{{attempts}} attempts - {{requests}} model requests", { attempts: memory.attempts ?? 0, requests: memory.requests ?? 0 })}`}
              {` - ${t("{{input}} input / {{output}} output tokens", { input: memory.input_tokens ?? 0, output: memory.output_tokens ?? 0 })}`}
            </p>
          </SettingsSection>
        )}
        <Link to="/setup">{t("Guided setup and environment checks")}</Link>
        {[
          ...(status.data?.app.capability_warnings ?? []),
          ...(status.data?.app.content_plugin_diagnostics ?? []),
        ].map((message) => (
          <p key={message}>{message}</p>
        ))}
        <p>{t("Version {{version}}", { version: status.data?.version })}</p>
        <InstallSettings />
      </details>
    </>
  );
}

export function AgentsPage() {
  const { t } = useTranslation();
  return (
    <>
      <SourcesPage
        kinds={["agent"]}
        title={t("Agents")}
        description={t(
          "Configure instructions, models and tools for each agent.",
        )}
      />
      <details className={layout.disclosure}>
        <summary>{t("Installed capabilities")}</summary>
        <InstalledCatalog kind="capability" />
      </details>
    </>
  );
}

export function ConnectionsPage() {
  const { t } = useTranslation();
  const sources = useSources();
  const root = sources.data?.sources.find(
    (item) => item.resource_kind === "root",
  );
  return (
    <>
      <PageHeader
        title={t("Connections")}
        description={t("Tools and services your agents can use.")}
      />
      <ConnectorsSection />
      <SourcesPage
        kinds={["mcp_server"]}
        headingLevel={2}
        title={t("MCP servers")}
        description={t("Defined in configuration files, selected per Agent.")}
      />
      <SourcesPage
        kinds={["harness_plugin"]}
        headingLevel={2}
        title={t("Plugins")}
      />
      <div className={layout.disclosures}>
        {root && (
          <details className={layout.disclosure}>
            <summary>
              <span>{t("Chat defaults")}</span>
              <small>{t("Connections and extensions for new chats")}</small>
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
          <summary>{t("Installed plugins")}</summary>
          <InstalledCatalog kind="harness_plugin" />
        </details>
      </div>
    </>
  );
}

export function EnvironmentsPage() {
  const { t } = useTranslation();
  const selectors = useSelectors();
  const sources = useSources();
  const [params] = useSearchParams();
  const tab = params.get("tab") ?? "local";
  const selectedTab = [
    "local",
    "devices",
    "profiles",
    "runtime",
    "browser",
  ].includes(tab)
    ? tab
    : "local";
  return (
    <div className={environmentStyles.page}>
      <PageHeader
        title={t("Environments")}
        description={t("Choose where and how your agents work.")}
      />
      <nav
        className={`${layout.tabs} a13n-scrollbar`}
        aria-label={t("Environment sections")}
      >
        {[
          ["local", t("Local execution")],
          ["devices", t("Devices")],
          ["profiles", t("Profiles & extensions")],
          ["runtime", t("Local runtime")],
          ["browser", t("Browser")],
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
                title={t("Execution defaults")}
                embedded
                section="execution"
              />
            ))}
          <p className={layout.hint}>
            {t("Choose folders and defaults in each project's settings.")}
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
              <h2 id="local-execution-profiles">{t("Local profiles")}</h2>
              <NewResourceButton
                kind="environment_profile"
                label={t("Add local profile")}
                variant="outline"
              />
            </div>
            <p className={layout.hint}>
              {t("Choose full access or a sandbox for local work.")}
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
                        ? t("Full control")
                        : item.mode === "sandbox"
                          ? t("Sandbox")
                          : item.provider_key}
                    </span>
                    <small>
                      {item.release_owned ? t("Built in") : t("Configured")}
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
              <span>{t("Environment providers")}</span>
              <small>{t("Create a profile from an installed provider")}</small>
            </summary>
            <InstalledCatalog kind="environment_provider" />
          </details>
          <SourcesPage
            kinds={["environment_run_extension"]}
            headingLevel={2}
            title={t("Run extensions")}
            description={t(
              "Extra features for runs. Choose them in project or global defaults.",
            )}
          />
        </>
      )}
      {selectedTab === "runtime" && <RuntimePage embedded />}
      {selectedTab === "browser" &&
        sources.data?.sources
          .filter((item) => item.resource_kind === "root")
          .map((root) => (
            <SourceDocument
              key={root.relative_path}
              path={root.relative_path}
              title={null}
              embedded
              section="browser"
            />
          ))}
    </div>
  );
}

export function InstalledCatalog({
  kind,
}: {
  kind: Schema<"CatalogReference">["kind"];
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [search, setSearch] = useState("");
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  const available = catalog.data?.filter((item) => item.kind === kind);
  const catalogCopy =
    kind === "capability"
      ? {
          search: t("Search installed capabilities"),
          noMatch: t("No matching installed capabilities."),
          empty: t("No installed capabilities."),
        }
      : kind === "environment_provider"
        ? {
            search: t("Search installed environment providers"),
            noMatch: t("No matching installed environment providers."),
            empty: t("No installed environment providers."),
          }
        : {
            search: t("Search installed plugins"),
            noMatch: t("No matching installed plugins."),
            empty: t("No installed plugins."),
          };
  const items = available?.filter((item) =>
    `${item.key} ${item.kind === "capability" ? capabilityLabel(item.key, t) : ""} ${item.distribution_name ?? ""}`
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
          label={catalogCopy.search}
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
                  ? capabilityLabel(item.key, t)
                  : item.key}
              </strong>
              <small>
                {item.distribution_name ??
                  (item.source === "pydantic" ? t("Built-in") : item.source)}
                {item.distribution_version
                  ? ` - ${item.distribution_version}`
                  : ""}
              </small>
            </div>
            {!item.configurable ? (
              <span className={layout.hint}>{t("No setup form")}</span>
            ) : resource ? (
              <NewResourceButton
                kind={resource}
                label={t("Configure")}
                variant="outline"
                initial={{
                  [kind === "environment_provider"
                    ? "provider_key"
                    : "plugin_key"]: item.key,
                }}
              />
            ) : (
              <span className={layout.hint}>{t("Available to agents")}</span>
            )}
          </div>
        ))}
      </div>
      {items?.length === 0 && (
        <p className={layout.hint}>
          {available?.length ? catalogCopy.noMatch : catalogCopy.empty}
        </p>
      )}
    </div>
  );
}
