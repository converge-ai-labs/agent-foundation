import { useTranslation } from "../i18n";
import { HelpTip } from "../shell/help-tip";
import { ModelIcon } from "../shell/model-identity";
import { SettingsTextField as TextField } from "../shell/settings";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { Link, useLocation } from "react-router";
import environments from "./environments.module.css";
import management from "./management.module.css";
import layout from "./settings.module.css";
import { AddModelButton } from "./models";
import { ModelSelectionField } from "./model-selection";
import { Button, FormField, Input, Switch, Textarea } from "a13n-ui";
import { ResourceChoice } from "./resource-choice";
import { useQuery } from "@tanstack/react-query";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import { SelectionField } from "./selection";
import { useRootConfiguration } from "./root-configuration";
import { BotFields } from "./bot-fields";
import { DictationFields } from "./dictation-fields";
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
  const { t } = useTranslation();
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
    return <p>{t("Edit this document in the source editor below.")}</p>;
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
      icon: <ModelIcon model={model} />,
    })) ?? [];
  const codexModels = (selectors.data?.models ?? [])
    .filter((model) => model.route.startsWith("openai-codex:"))
    .map((model) => ({
      value: model.model_id,
      label: model.name,
      icon: <ModelIcon model={model} />,
    }));
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
    emptyLabel = t("Use default"),
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
  const strings = (path: string[]) => {
    let selected: unknown = document.toJS();
    for (const key of path)
      selected =
        typeof selected === "object" && selected !== null
          ? (selected as Record<string, unknown>)[key]
          : undefined;
    return Array.isArray(selected)
      ? selected.filter((value): value is string => typeof value === "string")
      : null;
  };
  const list = (
    label: string,
    path: string[],
    options: { value: string; label: string }[],
  ) => {
    const values = strings(path);
    return (
      <SelectionField
        key={path.join(".")}
        label={label}
        inherited={
          kind === "agent"
            ? root.configuration
              ? t("{{items}} - Instance defaults for new chats", {
                  items:
                    (root.configuration.defaults?.[path[0]] ?? [])
                      .map(
                        (id: string) =>
                          options.find((option) => option.value === id)
                            ?.label ?? id,
                      )
                      .join(", ") || t("None"),
                })
              : t("Loading instance defaults…")
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
        t("Plugins"),
        [...prefix, "harness_plugins"],
        selectors.data?.harness_plugins.map((item) => ({
          value: item.resource_id,
          label: item.name,
        })) ?? [],
      )}
      {list(
        t("MCP servers"),
        [...prefix, "mcp_servers"],
        selectors.data?.mcp_servers.map((item) => ({
          value: item.resource_id,
          label: item.name,
        })) ?? [],
      )}
      {kind !== "agent" &&
        list(
          t("Run extensions"),
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
      <SettingsSection variant="grouped" description={t("Used for new chats.")}>
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
        <SettingsSection title={t("General")}>
          {field(t("Name"), ["name"])}
        </SettingsSection>
      )}
      {kind === "model" && (
        <ModelFields
          source={source}
          onChange={onChange}
          onReady={onReady}
          nameField={field(t("Name"), ["name"])}
        />
      )}
      {kind === "agent" && (
        <>
          <SettingsSection variant="grouped" title={t("General")}>
            {field(t("Name"), ["name"])}
            <ResourceChoice
              label={t("Model")}
              value={text(["model"])}
              loading={selectors.isPending || sources.isPending}
              onValueChange={(value) => set(["model"], value || undefined)}
              options={[{ value: "", label: t("Not connected") }, ...models]}
              actions={
                <AddModelButton compact onSaved={(id) => set(["model"], id)} />
              }
            />
          </SettingsSection>
          <SettingsSection
            variant="plain"
            title={t("Instructions")}
            description={t("Extra instructions for this agent.")}
          >
            <Textarea
              aria-label={t("Additional instructions")}
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
            variant="grouped"
            title={t("Chat defaults")}
            description={t(
              "Used by new chats. Existing chats keep their settings.",
            )}
          >
            {scalar(t("Default agent"), ["defaults", "agent"], agentOptions)}
            {referenceLists(["defaults"])}
          </SettingsSection>
          <SettingsSection
            variant="plain"
            title={t("Environments")}
            description={t(
              "Used by new chats, or when you apply Project defaults.",
            )}
          >
            <div className={environments.project}>
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
                  {t("Fix the folder list in Advanced configuration first.")}
                </p>
              )}
              <details className={environments.applyDetails}>
                <summary>{t("When applying to existing chats")}</summary>
                <p>
                  {rawBindings == null
                    ? t(
                        "New chats add no remote environments. Existing chats keep theirs.",
                      )
                    : bindings.length
                      ? t(
                          "Applying these defaults replaces the chat's remote environments.",
                        )
                      : t(
                          "Applying these defaults removes the chat's remote environments.",
                        )}
                </p>
                <div>
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
                      {t("Local only")}
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
                      {t("Keep current")}
                    </Button>
                  )}
                </div>
              </details>
            </div>
          </SettingsSection>
        </>
      )}
      {!kind &&
        document.has("schema_version") &&
        !document.has("mcpServers") && (
          <>
            <SettingsSection variant="grouped" title={t("Chats")}>
              {scalar(t("Default agent"), ["defaults", "agent"], agentOptions)}
            </SettingsSection>
            <SettingsSection
              title={
                <span className="flex items-center gap-1">
                  {t("Sidekick")}
                  <HelpTip label={t("About Sidekick")}>
                    {t(
                      "A Sidekick opens a separate chat in your chat list. Use it for work that needs its own conversation, your decisions or later follow-up.",
                    )}
                  </HelpTip>
                </span>
              }
            >
              {scalar(
                t("Sidekick agent"),
                ["webui", "sidekick", "agent"],
                agentOptions,
                t("Use current agent"),
              )}
              {scalar(
                t("Sidekick model"),
                ["webui", "sidekick", "model"],
                models,
                t("Use agent model"),
              )}
            </SettingsSection>
            <SettingsSection
              title={
                <span className="flex items-center gap-1">
                  {t("Subagents")}
                  <HelpTip label={t("About subagents")}>
                    {t(
                      "Subagents run in the background inside the current chat. They do not add chats to your list. Follow them in Background work. Their results return to the main agent automatically.",
                    )}
                  </HelpTip>
                </span>
              }
            >
              <SettingsRow
                label={t("Let the agent create subagents")}
                controlId="dynamic-subagents-enabled"
              >
                <Switch
                  id="dynamic-subagents-enabled"
                  checked={get(["subagents", "dynamic", "enabled"]) === true}
                  onCheckedChange={(enabled) =>
                    set(
                      ["subagents", "dynamic", "enabled"],
                      enabled || undefined,
                    )
                  }
                />
              </SettingsRow>
              {get(["subagents", "dynamic", "enabled"]) === true && (
                <ModelSelectionField
                  label={t("Models for created subagents")}
                  description={t(
                    "The agent can choose from these. With none selected, subagents use the main agent's model.",
                  )}
                  models={selectors.data?.models ?? []}
                  value={
                    strings(["subagents", "dynamic", "allowed_models"]) ?? []
                  }
                  onChange={(value) =>
                    set(["subagents", "dynamic", "allowed_models"], value)
                  }
                />
              )}
              <SettingsRow
                label={t("Maximum running subagents")}
                controlId="subagents-max-concurrent"
                description={t("Shared by all subagents of one chat.")}
              >
                <Input
                  id="subagents-max-concurrent"
                  type="number"
                  min={1}
                  step={1}
                  className={styles.settingControl}
                  value={Number(get(["subagents", "max_concurrent"]) ?? 8)}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isInteger(value) && value > 0)
                      set(
                        ["subagents", "max_concurrent"],
                        value === 8 ? undefined : value,
                      );
                  }}
                />
              </SettingsRow>
            </SettingsSection>
            <DictationFields source={source} onChange={onChange} />
            <SettingsSection variant="grouped" title={t("Memory")}>
              <SettingsRow
                label={t("File memory")}
                controlId="memory-enabled"
                stackOnNarrow={false}
                description={t(
                  "Remember preferences and keep knowledge for each project.",
                )}
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
                    label={t("Automatic organization")}
                    controlId="memory-organization-enabled"
                    stackOnNarrow={false}
                    description={t(
                      "Use extra model requests to keep memory files organized.",
                    )}
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
                    <summary>{t("Memory updates")}</summary>
                    <div>
                      {scalar(
                        t("Update model"),
                        ["memory", "auto_organize", "model"],
                        models,
                        t("Use default agent model"),
                      )}
                      <div className={styles.fieldGroup}>
                        <FormField
                          label={t("Update instructions")}
                          description={t(
                            "How to update and organize memory files.",
                          )}
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
            <section id="bot">
              <BotFields source={source} onChange={onChange} />
            </section>
            <section id="live">
              <SettingsSection variant="grouped" title={t("Live voice")}>
                <SettingsRow
                  label={t("Live")}
                  controlId="live-enabled"
                  stackOnNarrow={false}
                  description={t(
                    "Talk to your agent using your Codex subscription.",
                  )}
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
                  <summary>{t("Voice settings")}</summary>
                  <div>
                    <ResourceChoice
                      label={t("Codex model")}
                      value={text(["live", "model"])}
                      loading={selectors.isPending || sources.isPending}
                      onValueChange={(value) =>
                        set(["live", "model"], value || undefined)
                      }
                      options={[
                        {
                          value: "",
                          label: codexModels.length
                            ? t("Choose a Codex model")
                            : t("Not configured"),
                        },
                        ...codexModels,
                      ]}
                      description={
                        !selectors.isPending && !codexModels.length ? (
                          <>
                            {t("Add a Codex subscription model in")}{" "}
                            <Link
                              className="underline underline-offset-2"
                              to="/settings/models"
                            >
                              {t("Models")}
                            </Link>{" "}
                            {t("to use live voice.")}
                          </>
                        ) : undefined
                      }
                    />
                    <SettingsRow label={t("Voice")}>
                      <ChoiceField
                        label={t("Voice")}
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
                      label={t("Workspace files")}
                      description={t(
                        "Let Live read and edit files in its workspace.",
                      )}
                    >
                      <ChoiceField
                        label={t("Live file access")}
                        hideLabel
                        value={
                          get(["live", "files_enabled"]) === false
                            ? "disabled"
                            : "enabled"
                        }
                        options={[
                          { value: "enabled", label: t("Enabled") },
                          { value: "disabled", label: t("Disabled") },
                        ]}
                        onValueChange={(value) =>
                          set(["live", "files_enabled"], value === "enabled")
                        }
                      />
                    </SettingsRow>
                    <div className={styles.fieldGroup}>
                      <FormField label={t("Live instructions")}>
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
          variant="grouped"
          title={
            kind === "environment_profile"
              ? t("Environment connection")
              : t("Plugin configuration")
          }
        >
          <ResourceChoice
            loading={catalog.isPending}
            label={
              kind === "environment_profile"
                ? t("Environment provider")
                : t("Plugin")
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
            field(t("Adapter key"), ["adapter_key"])}
          <p className={styles.sectionNote}>
            {t("Set this plugin's options in Advanced configuration below.")}
          </p>
        </SettingsSection>
      )}
      {kind === "mcp_server" && (
        <SettingsSection variant="grouped" title={t("Server connection")}>
          <ChoiceField
            label={t("Transport")}
            value={document.hasIn(["transport", "url"]) ? "http" : "stdio"}
            options={[
              { value: "stdio", label: t("Local command") },
              { value: "http", label: t("Remote HTTP") },
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
            ? field(t("Server URL"), ["transport", "url"])
            : field(t("Command"), ["transport", "command"])}
          <p className={styles.sectionNote}>
            {t(
              "Add arguments, environment variables or headers in Advanced configuration. Keep a copy; saved connection details are hidden.",
            )}
          </p>
        </SettingsSection>
      )}
    </div>
  );
}
