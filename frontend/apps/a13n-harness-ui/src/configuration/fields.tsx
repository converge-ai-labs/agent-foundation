import {
  ChoiceField,
  FormField,
  SettingsRow,
  SettingsSection,
  Textarea,
} from "a13n-ui";
import { Link } from "react-router";
import { ResourceChoice } from "./resource-choice";
import { useQuery } from "@tanstack/react-query";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";
import { AgentFields } from "./agent-fields";
import { ProjectFolders } from "./project-folders";

export function ResourceFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (value: string) => void;
}) {
  const selectors = useSelectors();
  const sources = useSources();
  const { client } = useTransport();
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
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
  const rawRoots = document.toJS().roots;
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
    sources.data?.sources
      .filter((item) => item.resource_kind === "model")
      .flatMap((item) =>
        item.resource_ids.map((id) => ({ value: id, label: id })),
      ) ?? [];
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
  return (
    <div className={styles.stack}>
      <ErrorNotice error={selectors.error || sources.error || catalog.error} />
      {kind && (
        <SettingsSection title="Identity">
          <div className={`${styles.formGrid} ${styles.fieldGroup}`}>
            {field(
              "Resource ID",
              ["id"],
              "Changing an ID can break references. Validation checks the complete configuration.",
            )}
            {field("Name", ["name"])}
          </div>
        </SettingsSection>
      )}
      {kind === "model" && (
        <SettingsSection title="Model connection">
          <div className={`${styles.stack} ${styles.fieldGroup}`}>
            {field(
              "Model route",
              ["route"],
              "Use a registered provider route, such as openai-responses:<model>. No model request is sent by validation.",
            )}
            <ChoiceField
              label="Authentication"
              value={text(["authentication", "kind"])}
              options={[
                { value: "api_key", label: "API key reference" },
                { value: "codex_subscription", label: "Codex account" },
                { value: "grok_subscription", label: "Grok account" },
              ]}
              onValueChange={(value) =>
                set(
                  ["authentication"],
                  value === "api_key"
                    ? { kind: value, credential_ref: "key-primary" }
                    : { kind: value },
                )
              }
            />
            {text(["authentication", "kind"]) === "api_key" && (
              <>
                <ChoiceField
                  label="Credential source"
                  value={
                    document.hasIn(["authentication", "env"])
                      ? "env"
                      : "credential_ref"
                  }
                  options={[
                    {
                      value: "credential_ref",
                      label: "Saved API key",
                    },
                    { value: "env", label: "Server environment variable" },
                  ]}
                  onValueChange={(value) =>
                    set(["authentication"], {
                      kind: "api_key",
                      [value]:
                        value === "env" ? "PROVIDER_API_KEY" : "key-primary",
                    })
                  }
                />
                {document.hasIn(["authentication", "env"]) ? (
                  field("Environment variable", ["authentication", "env"])
                ) : (
                  <CredentialReference
                    value={text(["authentication", "credential_ref"])}
                    onChange={(value) =>
                      set(["authentication", "credential_ref"], value)
                    }
                  />
                )}
              </>
            )}
            <p>
              Provider settings, base URL and context characteristics remain
              available in advanced YAML.
            </p>
          </div>
        </SettingsSection>
      )}
      {kind === "agent" && (
        <>
          <SettingsSection title="Agent behavior">
            {scalar("Model", ["model"], models, "Not connected")}
            <div className={styles.fieldGroup}>
              <FormField label="Instructions">
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
          <SettingsSection
            title="Connections"
            description="Select reusable plugins and MCP servers for this agent."
          >
            {referenceLists([])}
          </SettingsSection>
          <AgentFields source={source} onChange={onChange} />
        </>
      )}
      {kind === "project" && (
        <>
          <SettingsSection title="Project folders">
            <div className={styles.fieldGroup}>
              {roots ? (
                <ProjectFolders
                  roots={roots}
                  onChange={(value) => set(["roots"], value)}
                />
              ) : (
                <p>
                  Repair the host root list in advanced YAML before using the
                  directory field.
                </p>
              )}
            </div>
          </SettingsSection>
          <SettingsSection
            title="Conversation defaults"
            description="Applies to new conversations. Existing conversations can explicitly apply Project defaults."
          >
            {scalar("Default agent", ["defaults", "agent"], agentOptions)}
            {scalar(
              "Default environment",
              ["defaults", "environment_profile"],
              environmentOptions,
            )}
            {referenceLists(["defaults"])}
          </SettingsSection>
        </>
      )}
      {!kind &&
        document.has("schema_version") &&
        !document.has("mcpServers") && (
          <>
            <SettingsSection title="Sidekick">
              <SettingsRow
                label="Sidekick"
                description="Add instructions for independent work in other conversations. Does not start work automatically. Thread and resource tools remain available when disabled."
              >
                <ChoiceField
                  label="Sidekick"
                  hideLabel
                  className={styles.settingControl}
                  value={get(["webui", "sidekick"]) ? "enabled" : "disabled"}
                  onValueChange={(value) =>
                    set(["webui", "sidekick"], value === "disabled" ? null : {})
                  }
                  options={[
                    { value: "disabled", label: "Disabled" },
                    { value: "enabled", label: "Enabled" },
                  ]}
                />
              </SettingsRow>
              {get(["webui", "sidekick"]) != null && (
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
            <SettingsSection
              title="Conversation defaults"
              description="Defaults are used for new conversations. Use default keeps the inherited selection; None selects no resources."
            >
              {scalar("Default agent", ["defaults", "agent"], agentOptions)}
              {scalar(
                "Default environment",
                ["defaults", "environment_profile"],
                environmentOptions,
              )}
              {referenceLists(["defaults"])}
            </SettingsSection>
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
            {kind === "environment_profile" && (
              <>
                {field("Provider schema version", ["provider_schema_version"])}
                {field("Adapter key", ["adapter_key"])}
              </>
            )}
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

function CredentialReference({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { client } = useTransport();
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  return (
    <div className={styles.stack}>
      <ResourceChoice
        label="Saved key name"
        value={value}
        loading={keys.isPending}
        onValueChange={onChange}
        placeholder="Choose a saved API key"
        options={(keys.data ?? []).map((key) => ({
          value: key.credential_ref,
          label: key.credential_ref,
        }))}
        description={
          <>
            <Link to="/settings/accounts">Manage API keys</Link>. Only key names
            are listed; secrets never appear in YAML. Your draft is retained
            while you navigate.
          </>
        }
      />
      <ErrorNotice error={keys.error} retry={() => void keys.refetch()} />
    </div>
  );
}
