import { useLocation } from "react-router";
import { PlugsConnected, Robot } from "@phosphor-icons/react";
import management from "./management.module.css";
import { useState } from "react";
import layout from "./settings.module.css";
import { AddModelButton } from "./models";
import {
  Button,
  ChoiceField,
  FormField,
  SettingsRow,
  SettingsSection,
  Textarea,
} from "a13n-ui";
import { ResourceChoice } from "./resource-choice";
import { useQuery } from "@tanstack/react-query";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";
import { useRootConfiguration } from "./root-configuration";
import { ModelFields } from "./model-editor";
import { AgentFields } from "./agent-fields";
import { EnvironmentsEditor } from "./environments";
import { DeviceFields } from "./device-fields";
import { BrowserFields } from "./browser-fields";
import { type EnvironmentBinding } from "./environment-bindings";

export function ResourceFields({
  source,
  onChange,
  connectionsOnly = false,
  onReady,
}: {
  onReady?: (ready: boolean) => void;
  connectionsOnly?: boolean;
  source: string;
  onChange: (value: string) => void;
}) {
  const location = useLocation();
  const [browserOpen, setBrowserOpen] = useState(location.hash === "#browser");
  const selectors = useSelectors();
  const sources = useSources();
  const { client } = useTransport();
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  const root = useRootConfiguration(
    readDocument(source)?.get("kind") === "agent",
  );
  const document = readDocument(source);
  if (!document)
    return (
      <p>
        Structured fields are unavailable for this document. Use the source
        editor below.
      </p>
    );
  const get = (path: string[]) => document.getIn(path);
  const text = (path: string[]) =>
    typeof get(path) === "string" ? String(get(path)) : "";
  const set = (path: string[], value: unknown) =>
    onChange(updateDocument(source, path, value));
  const kind = text(["kind"]);
  const raw = document.toJS();
  const rawBindings = raw.defaults?.environment_bindings;
  const bindings: EnvironmentBinding[] = Array.isArray(rawBindings)
    ? rawBindings
    : [];
  const setEnvironments = (
    next: EnvironmentBinding[] | undefined,
    defaultEnvironment: string | null,
  ) => {
    let updated = updateDocument(
      source,
      ["defaults", "environment_bindings"],
      next,
    );
    updated = updateDocument(
      updated,
      ["defaults", "default_environment"],
      defaultEnvironment ?? undefined,
    );
    onChange(updated);
  };
  const rawRoots = raw.roots ?? [];
  const roots: { path: string }[] | null =
    Array.isArray(rawRoots) &&
    rawRoots.every((root) => root && typeof root.path === "string")
      ? rawRoots
      : null;
  const field = (label: string, path: string[], description?: string) => (
    <TextField
      key={path.join(".")}
      label={label}
      value={text(path)}
      onChange={(value) => set(path, value)}
      description={description}
    />
  );
  const models =
    selectors.data?.models?.map((model) => ({
      value: model.model_id,
      label: model.name,
    })) ?? [];
  const agentOptions =
    selectors.data?.agents.map((agent) => ({
      value: agent.agent_id,
      label: agent.name,
    })) ?? [];
  const environmentOptions =
    selectors.data?.environments.map((environment) => ({
      value: environment.profile_id,
      label: environment.name,
    })) ?? [];
  const scalar = (
    label: string,
    path: string[],
    options: { value: string; label: string }[],
    emptyLabel = "Use default",
  ) => (
    <ResourceChoice
      row
      label={label}
      value={text(path)}
      loading={selectors.isPending || sources.isPending}
      onValueChange={(value) => set(path, value || undefined)}
      options={[{ value: "", label: emptyLabel }, ...options]}
    />
  );
  const list = (
    label: string,
    path: string[],
    options: { value: string; label: string }[],
  ) => {
    const raw = document.toJS() as Record<string, unknown>;
    let selected: unknown = raw;
    for (const key of path)
      selected =
        typeof selected === "object" && selected !== null
          ? (selected as Record<string, unknown>)[key]
          : undefined;
    const values = Array.isArray(selected)
      ? selected.filter((value): value is string => typeof value === "string")
      : null;
    return (
      <SelectionField
        key={path.join(".")}
        label={label}
        inherited={
          kind === "agent"
            ? root.configuration
              ? `${(root.configuration.defaults?.[path[0]] ?? []).map((id: string) => options.find((option) => option.value === id)?.label ?? id).join(", ") || "None"} · Instance defaults for new conversations`
              : "Loading instance defaults…"
            : undefined
        }
        value={values}
        options={options}
        onChange={(value) => set(path, value)}
      />
    );
  };

  const referenceLists = (prefix: string[]) => (
    <>
      {list(
        "Harness plugins",
        [...prefix, "harness_plugins"],
        selectors.data?.harness_plugins.map((item) => ({
          value: item.resource_id,
          label: item.name,
        })) ?? [],
      )}
      {list(
        "MCP servers",
        [...prefix, "mcp_servers"],
        selectors.data?.mcp_servers.map((item) => ({
          value: item.resource_id,
          label: item.name,
        })) ?? [],
      )}
      {kind !== "agent" &&
        list(
          "Run extensions",
          [...prefix, "environment_run_extensions"],
          selectors.data?.environment_run_extensions.map((item) => ({
            value: item.resource_id,
            label: item.name,
          })) ?? [],
        )}
    </>
  );
  if (connectionsOnly)
    return (
      <SettingsSection description="Used for new conversations.">
        <ErrorNotice error={selectors.error || sources.error} />
        {referenceLists(["defaults"])}
      </SettingsSection>
    );
  return (
    <div className={styles.stack}>
      <ErrorNotice
        error={selectors.error || sources.error || catalog.error || root.error}
      />
      {kind && kind !== "agent" && <div>{field("Name", ["name"])}</div>}
      {kind === "model" && (
        <SettingsSection
          title={
            <span className={management.sectionTitle}>
              <PlugsConnected aria-hidden="true" weight="duotone" />
              Model connection
            </span>
          }
        >
          <div className={styles.fieldGroup}>
            <ModelFields
              source={source}
              onChange={onChange}
              onReady={onReady}
            />
          </div>
        </SettingsSection>
      )}
      {kind === "agent" && (
        <>
          <SettingsSection
            title={
              <span className={management.sectionTitle}>
                <Robot aria-hidden="true" weight="duotone" />
                Basics
              </span>
            }
          >
            <div className={`${styles.fieldGroup} ${styles.stack}`}>
              {field("Name", ["name"])}
              <div className={management.modelPickerRow}>
                <ResourceChoice
                  label="Model"
                  value={text(["model"])}
                  loading={selectors.isPending || sources.isPending}
                  onValueChange={(value) => set(["model"], value || undefined)}
                  options={[{ value: "", label: "Not connected" }, ...models]}
                />
                <AddModelButton onSaved={(id) => set(["model"], id)} />
              </div>
              <FormField label="Additional instructions">
                <Textarea
                  value={text(["instructions"])}
                  onChange={(event) =>
                    set(["instructions"], event.target.value)
                  }
                  rows={7}
                />
              </FormField>
            </div>
          </SettingsSection>
          <AgentFields
            source={source}
            onChange={onChange}
            connections={referenceLists([])}
          />
        </>
      )}
      {kind === "device" && (
        <DeviceFields source={source} onChange={onChange} />
      )}
      {kind === "project" && (
        <>
          <SettingsSection
            title="Conversation defaults"
            description="Applies to new conversations. Existing conversations can explicitly apply Project defaults."
          >
            {scalar("Default agent", ["defaults", "agent"], agentOptions)}
            {referenceLists(["defaults"])}
          </SettingsSection>
          <SettingsSection
            title="Environments"
            description="Project defaults are copied into new conversations. Applying them to an existing Thread changes only specified selections."
          >
            <div className={styles.fieldGroup}>
              <p className="mb-3 text-sm text-muted-foreground">
                {rawBindings == null
                  ? "Device environments are unspecified: new conversations add none; applying Project defaults keeps the Thread's selection."
                  : bindings.length
                    ? "This selection replaces the Thread's added environments when Project defaults are applied."
                    : "Explicitly empty: applying Project defaults removes the Thread's added environments."}
              </p>
              {roots ? (
                <EnvironmentsEditor
                  value={{
                    local_roots: roots.map((root) => root.path),
                    environment_profile_id:
                      text(["defaults", "environment_profile"]) || null,
                    environment_bindings: bindings,
                    default_environment:
                      text(["defaults", "default_environment"]) || null,
                  }}
                  profiles={selectors.data?.environments}
                  allowUnsetDefault
                  requireWorkspace
                  onChange={(patch) => {
                    let updated = source;
                    if (patch.local_roots !== undefined)
                      updated = updateDocument(
                        updated,
                        ["roots"],
                        patch.local_roots?.map((path) => ({ path })),
                      );
                    if ("environment_profile_id" in patch)
                      updated = updateDocument(
                        updated,
                        ["defaults", "environment_profile"],
                        patch.environment_profile_id || undefined,
                      );
                    // Default-only edits keep an unspecified collection unspecified.
                    if (
                      patch.environment_bindings !== undefined &&
                      !(
                        patch.environment_bindings === bindings &&
                        rawBindings == null
                      )
                    )
                      updated = updateDocument(
                        updated,
                        ["defaults", "environment_bindings"],
                        patch.environment_bindings,
                      );
                    if (patch.default_environment !== undefined)
                      updated = updateDocument(
                        updated,
                        ["defaults", "default_environment"],
                        patch.default_environment || undefined,
                      );
                    onChange(updated);
                  }}
                />
              ) : (
                <p>
                  Repair the host root list in advanced YAML before using the
                  directory field.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2 [&_button]:h-auto [&_button]:whitespace-normal [&_button]:text-left">
                {rawBindings == null && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() =>
                      setEnvironments(
                        [],
                        text(["defaults", "default_environment"]) || null,
                      )
                    }
                  >
                    Explicitly select no added environments
                  </Button>
                )}
                {(rawBindings != null ||
                  text(["defaults", "default_environment"])) && (
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={!roots?.length}
                    onClick={() => setEnvironments(undefined, null)}
                  >
                    Leave Thread environments and default unchanged
                  </Button>
                )}
              </div>
            </div>
          </SettingsSection>
        </>
      )}
      {!kind &&
        document.has("schema_version") &&
        !document.has("mcpServers") && (
          <>
            <SettingsSection
              title="Conversation defaults"
              description="Used for new conversations."
            >
              {scalar("Default agent", ["defaults", "agent"], agentOptions)}
            </SettingsSection>
            <details
              id="browser"
              onToggle={(event) => setBrowserOpen(event.currentTarget.open)}
              className={layout.disclosure}
              open={location.hash === "#browser" || undefined}
            >
              <summary>
                Host browser<small>Browser and VNC</small>
              </summary>
              <BrowserFields
                source={source}
                onChange={onChange}
                active={browserOpen}
              />
            </details>
            <details
              id="live"
              className={layout.disclosure}
              open={location.hash === "#live" || undefined}
            >
              <summary>
                Live voice<small>Model, voice and workspace access</small>
              </summary>
              <SettingsSection
                title="Live voice"
                description="Talk to your agent using your Codex subscription."
              >
                <SettingsRow
                  label="Live"
                  description="Start voice conversations from the workspace."
                >
                  <ChoiceField
                    label="Live"
                    hideLabel
                    className={styles.settingControl}
                    value={
                      get(["live", "enabled"]) === true ? "enabled" : "disabled"
                    }
                    options={[
                      { value: "disabled", label: "Disabled" },
                      { value: "enabled", label: "Enabled" },
                    ]}
                    onValueChange={(value) =>
                      set(["live", "enabled"], value === "enabled")
                    }
                  />
                </SettingsRow>
                {scalar(
                  "Live Codex Model",
                  ["live", "model"],
                  (selectors.data?.models ?? [])
                    .filter((model) => model.route.startsWith("openai-codex:"))
                    .map((model) => ({
                      value: model.model_id,
                      label: model.name,
                    })),
                  "Choose a Codex Model",
                )}
                <SettingsRow label="Voice">
                  <ChoiceField
                    label="Voice"
                    hideLabel
                    className={styles.settingControl}
                    value={text(["live", "voice"]) || "cove"}
                    options={[
                      "cove",
                      "juniper",
                      "maple",
                      "spruce",
                      "ember",
                      "vale",
                      "breeze",
                      "arbor",
                      "sol",
                      "aube",
                      "bubbie",
                      "haetsal",
                      "hanul",
                      "himari",
                      "leher",
                      "miko",
                      "neer",
                      "porto",
                      "rindo",
                      "rio",
                      "rivage",
                      "selva",
                      "sonna",
                      "tinta",
                      "viola",
                    ].map((value) => ({
                      value,
                      label: value[0]!.toUpperCase() + value.slice(1),
                    }))}
                    onValueChange={(value) => set(["live", "voice"], value)}
                  />
                </SettingsRow>
                <SettingsRow
                  label="Workspace files"
                  description="Let Live read and edit files in its workspace."
                >
                  <ChoiceField
                    label="Live file access"
                    hideLabel
                    className={styles.settingControl}
                    value={
                      get(["live", "files_enabled"]) === false
                        ? "disabled"
                        : "enabled"
                    }
                    options={[
                      { value: "enabled", label: "Enabled" },
                      { value: "disabled", label: "Disabled" },
                    ]}
                    onValueChange={(value) =>
                      set(["live", "files_enabled"], value === "enabled")
                    }
                  />
                </SettingsRow>
                <div className={styles.fieldGroup}>
                  <FormField label="Live instructions">
                    <Textarea
                      value={text(["live", "instructions"])}
                      onChange={(event) =>
                        set(["live", "instructions"], event.target.value)
                      }
                      rows={3}
                    />
                  </FormField>
                </div>
              </SettingsSection>
            </details>
            <details className={layout.disclosure}>
              <summary>
                Memory<small>Shared knowledge and automatic organization</small>
              </summary>
              <SettingsSection
                title="Memory"
                description="Remember preferences across conversations and keep separate knowledge for each Project."
              >
                <SettingsRow
                  label="File memory"
                  description="Use saved memory in future runs."
                >
                  <ChoiceField
                    label="File memory"
                    hideLabel
                    className={styles.settingControl}
                    value={
                      get(["memory", "enabled"]) !== false
                        ? "enabled"
                        : "disabled"
                    }
                    onValueChange={(value) =>
                      set(["memory", "enabled"], value === "enabled")
                    }
                    options={[
                      { value: "enabled", label: "Enabled" },
                      { value: "disabled", label: "Disabled" },
                    ]}
                  />
                </SettingsRow>
                {get(["memory", "enabled"]) !== false && (
                  <>
                    <SettingsRow
                      label="Automatic organization"
                      description="Automatically organize updated memory files in WebUI. Uses additional model requests."
                    >
                      <ChoiceField
                        label="Automatic organization"
                        hideLabel
                        className={styles.settingControl}
                        value={
                          get(["memory", "auto_organize", "enabled"]) !== false
                            ? "enabled"
                            : "disabled"
                        }
                        onValueChange={(value) =>
                          set(
                            ["memory", "auto_organize", "enabled"],
                            value === "enabled",
                          )
                        }
                        options={[
                          { value: "enabled", label: "Enabled" },
                          { value: "disabled", label: "Disabled" },
                        ]}
                      />
                    </SettingsRow>
                    {scalar(
                      "Organization model",
                      ["memory", "auto_organize", "model"],
                      models,
                      "Use global default Agent's model",
                    )}
                    <div className={styles.fieldGroup}>
                      <FormField
                        label="Organization instructions"
                        description="Instructions for organizing memory."
                      >
                        <Textarea
                          value={text([
                            "memory",
                            "auto_organize",
                            "instructions",
                          ])}
                          rows={4}
                          onChange={(event) =>
                            set(
                              ["memory", "auto_organize", "instructions"],
                              event.target.value,
                            )
                          }
                        />
                      </FormField>
                    </div>
                  </>
                )}
              </SettingsSection>
            </details>
            <details className={layout.disclosure}>
              <summary>
                Sidekick<small>Defaults for delegated conversations</small>
              </summary>
              <SettingsSection title="Sidekick">
                <SettingsRow
                  label="Sidekick"
                  description="Guide the agent to delegate work to separate conversations."
                >
                  <ChoiceField
                    label="Sidekick"
                    hideLabel
                    className={styles.settingControl}
                    value={
                      get(["webui", "sidekick"]) !== null
                        ? "enabled"
                        : "disabled"
                    }
                    onValueChange={(value) =>
                      set(
                        ["webui", "sidekick"],
                        value === "disabled" ? null : {},
                      )
                    }
                    options={[
                      { value: "disabled", label: "Disabled" },
                      { value: "enabled", label: "Enabled" },
                    ]}
                  />
                </SettingsRow>
                {get(["webui", "sidekick"]) !== null && (
                  <>
                    {scalar(
                      "Sidekick agent",
                      ["webui", "sidekick", "agent"],
                      agentOptions,
                      "Inherit current agent",
                    )}
                    {scalar(
                      "Sidekick model",
                      ["webui", "sidekick", "model"],
                      models,
                      "Use agent model",
                    )}
                  </>
                )}
              </SettingsSection>
            </details>
          </>
        )}
      {[
        "harness_plugin",
        "environment_run_extension",
        "environment_profile",
      ].includes(kind) && (
        <SettingsSection
          title={
            kind === "environment_profile"
              ? "Environment connection"
              : "Plugin configuration"
          }
        >
          <div className={`${styles.stack} ${styles.fieldGroup}`}>
            <ResourceChoice
              loading={catalog.isPending}
              label={
                kind === "environment_profile"
                  ? "Environment provider"
                  : "Plugin"
              }
              value={text([
                kind === "harness_plugin"
                  ? "plugin_key"
                  : kind === "environment_profile"
                    ? "provider_key"
                    : "extension_key",
              ])}
              options={(catalog.data ?? [])
                .filter(
                  (item) =>
                    item.kind ===
                      (kind === "environment_profile"
                        ? "environment_provider"
                        : kind) && item.configurable,
                )
                .map((item) => ({ value: item.key, label: item.key }))}
              onValueChange={(value) =>
                set(
                  [
                    kind === "harness_plugin"
                      ? "plugin_key"
                      : kind === "environment_profile"
                        ? "provider_key"
                        : "extension_key",
                  ],
                  value,
                )
              }
            />
            {kind === "environment_profile" &&
              field("Adapter key", ["adapter_key"])}
            <p>
              Configure plugin-specific options in the configuration file below.
              Provider and adapter settings vary by plugin.
            </p>
          </div>
        </SettingsSection>
      )}
      {kind === "mcp_server" && (
        <SettingsSection title="Server connection">
          <div className={`${styles.stack} ${styles.fieldGroup}`}>
            <ChoiceField
              label="Transport"
              value={document.hasIn(["transport", "url"]) ? "http" : "stdio"}
              options={[
                { value: "stdio", label: "Local command" },
                { value: "http", label: "Remote HTTP" },
              ]}
              onValueChange={(value) =>
                set(
                  ["transport"],
                  value === "http"
                    ? { url: "https://" }
                    : { command: "", arguments: [] },
                )
              }
            />
            {document.hasIn(["transport", "url"])
              ? field("Server URL", ["transport", "url"])
              : field("Command", ["transport", "command"])}
            <p>
              Arguments and environment/header references belong in advanced
              YAML. Saved MCP source cannot be read back through this API.
            </p>
          </div>
        </SettingsSection>
      )}
    </div>
  );
}
