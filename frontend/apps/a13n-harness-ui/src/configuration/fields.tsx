import { ChoiceField, FormField, SettingsRow, Textarea } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";
import { AgentFields } from "./agent-fields";

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
    <SettingsRow label={label}>
      <ChoiceField
        label={label}
        hideLabel
        className={styles.settingControl}
        value={text(path) || "__default"}
        onValueChange={(value) =>
          set(path, value === "__default" ? undefined : value)
        }
        options={[{ value: "__default", label: emptyLabel }, ...options]}
      />
    </SettingsRow>
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
        <div className={styles.formGrid}>
          {field(
            "Resource ID",
            ["id"],
            "Changing an ID can break references. Validation checks the complete configuration.",
          )}
          {field("Name", ["name"])}
        </div>
      )}
      {kind === "model" && (
        <>
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
                  text(["authentication", "env"]) ? "env" : "credential_ref"
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
              {text(["authentication", "env"])
                ? field("Environment variable", ["authentication", "env"])
                : field(
                    "Saved key name",
                    ["authentication", "credential_ref"],
                    "Store the secret in Accounts & API keys, never in YAML.",
                  )}
            </>
          )}
          <p>
            Provider settings, base URL and context characteristics remain
            available in advanced YAML.
          </p>
        </>
      )}
      {kind === "agent" && (
        <>
          {scalar("Model", ["model"], models, "Not connected")}
          <FormField label="Instructions">
            <Textarea
              value={text(["instructions"])}
              onChange={(event) => set(["instructions"], event.target.value)}
              rows={7}
            />
          </FormField>
          {referenceLists([])}
          <AgentFields source={source} onChange={onChange} />
        </>
      )}
      {kind === "project" && (
        <>
          {roots ? (
            <FormField
              label="Host root directories"
              description="One existing absolute directory per line. These are server paths, not browser paths or an Agent permission boundary."
            >
              <Textarea
                value={roots.map((root) => root.path).join("\n")}
                onChange={(event) =>
                  set(
                    ["roots"],
                    event.target.value.split("\n").map((path) => ({ path })),
                  )
                }
                rows={3}
              />
            </FormField>
          ) : (
            <p>
              Repair the host root list in advanced YAML before using the
              directory field.
            </p>
          )}
          {scalar("Default agent", ["defaults", "agent"], agentOptions)}
          {scalar(
            "Default environment",
            ["defaults", "environment_profile"],
            environmentOptions,
          )}
          {referenceLists(["defaults"])}
        </>
      )}
      {!kind &&
        document.has("schema_version") &&
        !document.has("mcpServers") && (
          <>
            {scalar("Default agent", ["defaults", "agent"], agentOptions)}
            {scalar(
              "Default environment",
              ["defaults", "environment_profile"],
              environmentOptions,
            )}
            {referenceLists(["defaults"])}
          </>
        )}
      {[
        "harness_plugin",
        "environment_run_extension",
        "environment_profile",
      ].includes(kind) && (
        <>
          <ChoiceField
            label={
              kind === "environment_profile" ? "Environment provider" : "Plugin"
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
        </>
      )}
      {kind === "mcp_server" && (
        <>
          <ChoiceField
            label="Transport"
            value={text(["transport", "url"]) ? "http" : "stdio"}
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
          {text(["transport", "url"])
            ? field("Server URL", ["transport", "url"])
            : field("Command", ["transport", "command"])}
          <p>
            Arguments and environment/header references belong in advanced YAML.
            Saved MCP source cannot be read back through this API.
          </p>
        </>
      )}
    </div>
  );
}
