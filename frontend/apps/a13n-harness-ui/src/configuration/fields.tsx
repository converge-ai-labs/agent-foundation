import { SettingsTextField as TextField } from "../shell/settings";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { useLocation } from "react-router";
import management from "./management.module.css";
import layout from "./settings.module.css";
import { AddModelButton } from "./models";
import { Button, FormField, Switch, Textarea } from "a13n-ui";
import { ResourceChoice } from "./resource-choice";
import { useQuery } from "@tanstack/react-query";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";
import { useRootConfiguration } from "./root-configuration";
import { ModelFields } from "./model-editor";
import { AgentFields } from "./agent-fields";
import { EnvironmentsEditor } from "./environments";
import { DeviceFields } from "./device-fields";
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
              ? `${(root.configuration.defaults?.[path[0]] ?? []).map((id: string) => options.find((option) => option.value === id)?.label ?? id).join(", ") || "None"} · Instance defaults for new chats`
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
      <SettingsSection variant="outlined" description="Used for new chats.">
        <ErrorNotice error={selectors.error || sources.error} />
        {referenceLists(["defaults"])}
      </SettingsSection>
    );
  return (
    <div className={styles.configurationFields}>
      <ErrorNotice
        error={selectors.error || sources.error || catalog.error || root.error}
      />
      {kind && kind !== "agent" && kind !== "model" && (
        <SettingsSection title="General">
          {field("Name", ["name"])}
        </SettingsSection>
      )}
      {kind === "model" && (
        <ModelFields
          source={source}
          onChange={onChange}
          onReady={onReady}
          nameField={field("Name", ["name"])}
        />
      )}
      {kind === "agent" && (
        <>
          <SettingsSection variant="outlined" title="General">
            {field("Name", ["name"])}
            <ResourceChoice
              label="Model"
              value={text(["model"])}
              loading={selectors.isPending || sources.isPending}
              onValueChange={(value) => set(["model"], value || undefined)}
              options={[{ value: "", label: "Not connected" }, ...models]}
              actions={
                <AddModelButton compact onSaved={(id) => set(["model"], id)} />
              }
            />
          </SettingsSection>
          <SettingsSection
            variant="plain"
            title="Instructions"
            description="Additional guidance for this agent, alongside its built-in instructions."
          >
            <Textarea
              aria-label="Additional instructions"
              variant="soft"
              value={text(["instructions"])}
              onChange={(event) => set(["instructions"], event.target.value)}
              rows={5}
              className={management.instructions}
            />
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
            variant="outlined"
            title="Chat defaults"
            description="Used by new chats. Existing chats keep their settings."
          >
            {scalar("Default agent", ["defaults", "agent"], agentOptions)}
            {referenceLists(["defaults"])}
          </SettingsSection>
          <SettingsSection
            variant="outlined"
            title="Environments"
            description="Used by new chats, or when you apply Project defaults."
          >
            <div className={styles.fieldGroup}>
              <p className="mb-3 text-sm text-muted-foreground">
                {rawBindings == null
                  ? "Unspecified: new chats add no remote environments; existing selections stay unchanged."
                  : bindings.length
                    ? "Applying defaults replaces the chat’s remote environments with this selection."
                    : "Empty selection: applying defaults removes the chat’s remote environments."}
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
                    Keep this chat's environments
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
            <SettingsSection variant="outlined" title="Chats">
              {scalar("Default agent", ["defaults", "agent"], agentOptions)}
              <SettingsRow
                label="Sidekick"
                controlId="sidekick-enabled"
                stackOnNarrow={false}
                description="Let your agent delegate work to separate chats."
              >
                <Switch
                  id="sidekick-enabled"
                  checked={get(["webui", "sidekick"]) !== null}
                  onCheckedChange={(enabled) =>
                    set(["webui", "sidekick"], enabled ? {} : null)
                  }
                />
              </SettingsRow>
              {get(["webui", "sidekick"]) !== null && (
                <details className={layout.groupDisclosure}>
                  <summary>Delegation defaults</summary>
                  <div>
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
                  </div>
                </details>
              )}
            </SettingsSection>
            <SettingsSection variant="outlined" title="Memory">
              <SettingsRow
                label="File memory"
                controlId="memory-enabled"
                stackOnNarrow={false}
                description="Remember preferences and keep knowledge for each project."
              >
                <Switch
                  id="memory-enabled"
                  checked={get(["memory", "enabled"]) !== false}
                  onCheckedChange={(enabled) =>
                    set(["memory", "enabled"], enabled)
                  }
                />
              </SettingsRow>
              {get(["memory", "enabled"]) !== false && (
                <>
                  <SettingsRow
                    label="Automatic organization"
                    controlId="memory-organization-enabled"
                    stackOnNarrow={false}
                    description="Organize updated memory files with additional model requests."
                  >
                    <Switch
                      id="memory-organization-enabled"
                      checked={
                        get(["memory", "auto_organize", "enabled"]) !== false
                      }
                      onCheckedChange={(enabled) =>
                        set(["memory", "auto_organize", "enabled"], enabled)
                      }
                    />
                  </SettingsRow>
                  <details className={layout.groupDisclosure}>
                    <summary>Organization settings</summary>
                    <div>
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
                            variant="soft"
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
                    </div>
                  </details>
                </>
              )}
            </SettingsSection>
            <section id="live">
              <SettingsSection variant="outlined" title="Live voice">
                <SettingsRow
                  label="Live"
                  controlId="live-enabled"
                  stackOnNarrow={false}
                  description="Talk to your agent using your Codex subscription."
                >
                  <Switch
                    id="live-enabled"
                    checked={get(["live", "enabled"]) === true}
                    onCheckedChange={(enabled) =>
                      set(["live", "enabled"], enabled)
                    }
                  />
                </SettingsRow>
                <details
                  className={layout.groupDisclosure}
                  open={location.hash === "#live" || undefined}
                >
                  <summary>Voice settings</summary>
                  <div>
                    {scalar(
                      "Codex model",
                      ["live", "model"],
                      (selectors.data?.models ?? [])
                        .filter((model) =>
                          model.route.startsWith("openai-codex:"),
                        )
                        .map((model) => ({
                          value: model.model_id,
                          label: model.name,
                        })),
                      "Choose a Codex model",
                    )}
                    <SettingsRow label="Voice">
                      <ChoiceField
                        label="Voice"
                        hideLabel
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
                          variant="soft"
                          value={text(["live", "instructions"])}
                          onChange={(event) =>
                            set(["live", "instructions"], event.target.value)
                          }
                          rows={3}
                        />
                      </FormField>
                    </div>
                  </div>
                </details>
              </SettingsSection>
            </section>
          </>
        )}
      {[
        "harness_plugin",
        "environment_run_extension",
        "environment_profile",
      ].includes(kind) && (
        <SettingsSection
          variant="outlined"
          title={
            kind === "environment_profile"
              ? "Environment connection"
              : "Plugin configuration"
          }
        >
          <ResourceChoice
            loading={catalog.isPending}
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
          {kind === "environment_profile" &&
            field("Adapter key", ["adapter_key"])}
          <p className={styles.sectionNote}>
            Configure plugin-specific options in the configuration file below.
            Provider and adapter settings vary by plugin.
          </p>
        </SettingsSection>
      )}
      {kind === "mcp_server" && (
        <SettingsSection variant="outlined" title="Server connection">
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
          <p className={styles.sectionNote}>
            Arguments and environment/header references belong in advanced YAML.
            Saved MCP source cannot be read back through this API.
          </p>
        </SettingsSection>
      )}
    </div>
  );
}
