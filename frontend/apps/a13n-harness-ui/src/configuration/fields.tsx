import { useTranslation } from "../i18n";
import { modifierLabel } from "../platform";
import { HelpTip } from "../shell/help-tip";
import { acpPresetBrand, ModelIcon } from "../shell/model-identity";
import {
  SettingsChoice as ChoiceField,
  SettingsRow,
  SettingsSection,
  SettingsTextField as TextField,
  SettingsToggle,
} from "../shell/settings";
import { Link, useLocation } from "react-router";
import environments from "./environments.module.css";
import management from "./management.module.css";
import layout from "./settings.module.css";
import { AddModelButton } from "./models";
import { AgentSelectionField } from "./agent-selection";
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
import { WebProviderFields } from "./web-providers";
import { AgentFields } from "./agent-fields";
import { EnvironmentsEditor } from "./environments";
import { DeviceFields } from "./device-fields";
import { type EnvironmentBinding } from "./environment-bindings";

export type RootSettingsGroup = "chats" | "agents" | "bot" | "memory";

export function ResourceFields({
  source,
  onChange,
  connectionsOnly = false,
  group,
  liveAvailable,
  onReady,
}: {
  onReady?: (ready: boolean) => void;
  connectionsOnly?: boolean;
  group?: RootSettingsGroup;
  liveAvailable?: boolean;
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
  const presets = useQuery({
    queryKey: ["acp-presets"],
    enabled: readDocument(source)?.get("kind") === "acp_agent",
    queryFn: ({ signal }) =>
      result(client.GET("/api/acp-agents/presets", { signal })),
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
  const allowedAgentsPath = ["agent_creation", "allowed_agents"];
  const defaultAgentPath = ["agent_creation", "default_agent"];
  const allowedModelsPath = ["agent_creation", "allowed_models"];
  const defaultModelPath = ["agent_creation", "default_model"];
  const legacyAllowedModelsPath = ["subagents", "dynamic", "allowed_models"];
  const legacyDefaultModelPath = ["subagents", "dynamic", "default_model"];
  const allowedAgentsRaw = strings(allowedAgentsPath);
  const allowedAgents = allowedAgentsRaw ?? [];
  const allowedModels =
    strings(allowedModelsPath) ?? strings(legacyAllowedModelsPath) ?? [];
  const defaultModel = document.hasIn(defaultModelPath)
    ? text(defaultModelPath)
    : text(legacyDefaultModelPath);
  const updateCreationModels = (
    nextAllowed: string[] | undefined,
    nextDefault: string | undefined,
  ) => {
    let updated = updateDocument(source, allowedModelsPath, nextAllowed);
    updated = updateDocument(updated, defaultModelPath, nextDefault);
    if (document.hasIn(legacyAllowedModelsPath))
      updated = updateDocument(updated, legacyAllowedModelsPath, undefined);
    if (document.hasIn(legacyDefaultModelPath))
      updated = updateDocument(updated, legacyDefaultModelPath, undefined);
    onChange(updated);
  };
  const dynamicSubagentsEnabled =
    get(["subagents", "dynamic", "enabled"]) === true;
  const creationAgentOptions = (selectors.data?.agents ?? [])
    .filter(
      (agent) =>
        allowedAgentsRaw === null || allowedAgentsRaw.includes(agent.agent_id),
    )
    .map((agent) => {
      const presetBrand =
        agent.kind === "acp_agent" ? acpPresetBrand(agent.preset) : undefined;
      const model =
        agent.kind === "acp_agent"
          ? undefined
          : (selectors.data?.models ?? []).find(
              (item) => item.model_id === agent.model_id,
            );
      return {
        value: agent.agent_id,
        label: agent.name,
        badge: agent.kind === "acp_agent" ? t("ACP agent") : undefined,
        icon: presetBrand ? (
          <ModelIcon modelIdentity={presetBrand} />
        ) : model ? (
          <ModelIcon model={model} />
        ) : undefined,
      };
    });
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
      {kind && kind !== "agent" && kind !== "acp_agent" && kind !== "model" && (
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
      {kind === "acp_agent" &&
        (() => {
          const presetValue = text(["preset"]) || "custom";
          const selectedPreset = presets.data?.find(
            (item) => item.id === presetValue,
          );
          const presetNames = new Set(
            (presets.data ?? []).map((item) => item.name),
          );
          const explicitCommand = text(["command"]);
          const explicitArgs = Array.isArray(raw.args)
            ? raw.args.map((item: unknown) => String(item))
            : undefined;
          const presetFallbackArgs =
            presetValue === "antigravity" ? ["acp"] : [];
          const resolvedArgs: readonly string[] =
            !explicitCommand && selectedPreset?.executable === "npx"
              ? [...(selectedPreset.args ?? []), ...(explicitArgs ?? [])]
              : (explicitArgs ?? selectedPreset?.args ?? presetFallbackArgs);
          const commandPreview = [
            explicitCommand || selectedPreset?.executable || "",
            ...resolvedArgs,
          ]
            .filter(Boolean)
            .join(" ");
          const commandFields = (
            <>
              <TextField
                label={t("Executable")}
                value={text(["command"])}
                onChange={(value) => set(["command"], value || undefined)}
                description={t(
                  "Use a command on the Host PATH or an absolute path. Leave blank to use the preset executable.",
                )}
              />
              <div className={styles.fieldGroup}>
                <FormField
                  label={t("Arguments")}
                  description={t(
                    "One argument per line. Antigravity’s skip-permissions flag grants unattended access as the Host account.",
                  )}
                >
                  <Textarea
                    aria-label={t("Arguments")}
                    variant="soft"
                    rows={3}
                    key={`${presetValue}:${Array.isArray(raw.args) ? raw.args.includes("--dangerously-skip-permissions") : ""}`}
                    defaultValue={
                      Array.isArray(raw.args) ? raw.args.join("\n") : ""
                    }
                    onChange={(event) =>
                      set(
                        ["args"],
                        event.target.value.split("\n").filter(Boolean),
                      )
                    }
                  />
                </FormField>
              </div>
            </>
          );
          return (
            <>
              <SettingsSection variant="grouped" title={t("General")}>
                {field(t("Name"), ["name"])}
                <ResourceChoice
                  label={t("ACP preset")}
                  value={presetValue}
                  onValueChange={(value) => {
                    const preset = presets.data?.find(
                      (item) => item.id === value,
                    );
                    const currentName = text(["name"]).trim();
                    let next = updateDocument(source, ["preset"], value);
                    if (
                      preset &&
                      (!currentName ||
                        currentName === "Untitled" ||
                        presetNames.has(currentName))
                    ) {
                      next = updateDocument(
                        next,
                        ["name"],
                        value === "custom"
                          ? t("Custom ACP Agent")
                          : preset.name,
                      );
                    }
                    if (value !== "custom") {
                      next = updateDocument(next, ["command"], undefined);
                      next = updateDocument(
                        next,
                        ["args"],
                        value === "antigravity"
                          ? ["acp", "--dangerously-skip-permissions"]
                          : undefined,
                      );
                    }
                    onChange(next);
                  }}
                  options={(presets.data ?? []).map((item) => {
                    const brand = acpPresetBrand(item.id);
                    return {
                      value: item.id,
                      label: !item.executable
                        ? t("Custom ACP Agent")
                        : item.name,
                      badge: !item.executable
                        ? undefined
                        : item.detected_path
                          ? t("Detected")
                          : t("Not installed"),
                      icon: brand ? (
                        <ModelIcon modelIdentity={brand} />
                      ) : undefined,
                    };
                  })}
                />
                {presetValue !== "custom" && commandPreview && (
                  <SettingsRow
                    label={t("Command")}
                    description={
                      <code style={{ overflowWrap: "anywhere" }}>
                        {commandPreview}
                      </code>
                    }
                  >
                    {!explicitCommand &&
                      selectedPreset &&
                      (!selectedPreset.detected_path &&
                      selectedPreset.install_url ? (
                        <a
                          target="_blank"
                          rel="noreferrer"
                          href={selectedPreset.install_url}
                        >
                          {t("Installation instructions")}
                        </a>
                      ) : (
                        <span>
                          {selectedPreset.detected_path
                            ? t("Detected")
                            : t("Not installed")}
                        </span>
                      ))}
                  </SettingsRow>
                )}
                {presetValue === "antigravity" && (
                  <SettingsToggle
                    label={t("Skip permission prompts")}
                    description={t(
                      "Allow unattended tool execution without interactive CLI prompts.",
                    )}
                    checked={resolvedArgs.includes(
                      "--dangerously-skip-permissions",
                    )}
                    onCheckedChange={(checked) => {
                      const base = resolvedArgs.filter(
                        (arg) => arg !== "--dangerously-skip-permissions",
                      );
                      set(
                        ["args"],
                        checked
                          ? [
                              base[0] ?? "acp",
                              "--dangerously-skip-permissions",
                              ...base.slice(1),
                            ]
                          : base,
                      );
                    }}
                  />
                )}
                <ErrorNotice error={presets.error} />
                {presetValue === "custom" ? (
                  commandFields
                ) : (
                  <details className={layout.groupDisclosure}>
                    <summary>{t("Customize command")}</summary>
                    <div>{commandFields}</div>
                  </details>
                )}
                {list(
                  t("MCP servers"),
                  ["mcp_servers"],
                  selectors.data?.mcp_servers.map((item) => ({
                    value: item.resource_id,
                    label: item.name,
                  })) ?? [],
                )}
              </SettingsSection>
              <SettingsSection variant="plain" title={t("Instructions")}>
                <Textarea
                  aria-label={t("Additional instructions")}
                  variant="soft"
                  value={text(["instructions"])}
                  onChange={(event) =>
                    set(["instructions"], event.target.value)
                  }
                  rows={5}
                  className={management.instructions}
                />
              </SettingsSection>
            </>
          );
        })()}
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
      {kind === "web_provider" && (
        <WebProviderFields source={source} onChange={onChange} />
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
            {(!group || group === "chats") && (
              <>
                <SettingsSection variant="grouped" title={t("Chat input")}>
                  <ChoiceField
                    label={t("Send messages with")}
                    description={t(
                      "Applies to all browsers using this instance.",
                    )}
                    value={text(["webui", "send_shortcut"]) || "enter"}
                    onValueChange={(value) =>
                      set(["webui", "send_shortcut"], value)
                    }
                    options={[
                      { value: "enter", label: t("Enter (default)") },
                      {
                        value: "mod-enter",
                        label: t("{{modifier}} + Enter", {
                          modifier: modifierLabel(),
                        }),
                      },
                    ]}
                  />
                </SettingsSection>
                <SettingsSection variant="grouped" title={t("New chats")}>
                  {scalar(
                    t("Default agent"),
                    ["defaults", "agent"],
                    agentOptions,
                  )}

                  <SettingsRow
                    label={t("Coordinate agents by default")}
                    controlId="coordinator-by-default"
                    stackOnNarrow={false}
                    description={t(
                      "Preselect Coordinate agents for new project chats. You can turn it off before sending the first message.",
                    )}
                  >
                    <Switch
                      id="coordinator-by-default"
                      checked={
                        get(["webui", "coordinator_by_default"]) === true
                      }
                      onCheckedChange={(enabled) =>
                        set(["webui", "coordinator_by_default"], enabled)
                      }
                    />
                  </SettingsRow>
                  <SettingsRow
                    label={t("Automatically generate chat titles")}
                    controlId="auto-chat-title"
                    stackOnNarrow={false}
                    description={t(
                      "Use an extra model request to name new chats. You can still generate a title manually when this is off.",
                    )}
                  >
                    <Switch
                      id="auto-chat-title"
                      checked={get(["auto_chat_title"]) !== false}
                      onCheckedChange={(enabled) =>
                        set(["auto_chat_title"], enabled)
                      }
                    />
                  </SettingsRow>
                  <ResourceChoice
                    label={t("Title generation model")}
                    value={text(["chat_title_model"])}
                    options={[
                      { value: "", label: t("Current chat model") },
                      ...models,
                    ]}
                    onValueChange={(value) =>
                      set(["chat_title_model"], value || undefined)
                    }
                  />
                </SettingsSection>
                <SettingsSection
                  variant="grouped"
                  title={t("Input predictions")}
                >
                  <SettingsRow
                    label={t("Suggest follow-ups")}
                    controlId="input-prediction-enabled"
                    stackOnNarrow={false}
                    description={t(
                      "An empty composer can use one extra request to the chat’s model after a reply. Accept a suggestion to fill the composer; nothing is sent automatically.",
                    )}
                  >
                    <Switch
                      id="input-prediction-enabled"
                      checked={get(["input_prediction", "enabled"]) === true}
                      onCheckedChange={(enabled) =>
                        set(["input_prediction", "enabled"], enabled)
                      }
                    />
                  </SettingsRow>
                </SettingsSection>
                <SettingsSection variant="grouped" title={t("Inbox")}>
                  <SettingsRow
                    label={t("Let agents post to Inbox")}
                    controlId="inbox-enabled"
                    stackOnNarrow={false}
                    description={t(
                      "Agents post results, questions and milestones to your Inbox and are reminded to do so. When this is off, new runs get no Inbox tool or reminders; existing items stay.",
                    )}
                  >
                    <Switch
                      id="inbox-enabled"
                      checked={get(["inbox", "enabled"]) !== false}
                      onCheckedChange={(enabled) =>
                        set(["inbox", "enabled"], enabled)
                      }
                    />
                  </SettingsRow>
                </SettingsSection>
                <DictationFields source={source} onChange={onChange} />
                {group === "chats" && (
                  <SettingsSection title={t("Archived chats")}>
                    <Link to="/archived">{t("View archived chats")}</Link>
                  </SettingsSection>
                )}
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
                    {get(["live", "enabled"]) === true && (
                      <>
                        {group === "chats" && liveAvailable && (
                          <Link to="/live">{t("Open Live")}</Link>
                        )}
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
                                  label:
                                    value[0]!.toUpperCase() + value.slice(1),
                                }))}
                                onValueChange={(value) =>
                                  set(["live", "voice"], value)
                                }
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
                                  set(
                                    ["live", "files_enabled"],
                                    value === "enabled",
                                  )
                                }
                              />
                            </SettingsRow>
                            <div className={styles.fieldGroup}>
                              <FormField label={t("Live instructions")}>
                                <Textarea
                                  variant="soft"
                                  value={text(["live", "instructions"])}
                                  onChange={(event) =>
                                    set(
                                      ["live", "instructions"],
                                      event.target.value,
                                    )
                                  }
                                  rows={3}
                                />
                              </FormField>
                            </div>
                          </div>
                        </details>
                      </>
                    )}
                  </SettingsSection>
                </section>
              </>
            )}
            {(!group || group === "agents") && (
              <>
                <SettingsSection title={t("Agent creation")}>
                  <AgentSelectionField
                    label={t("Allowed agents")}
                    description={
                      allowedAgentsRaw === null
                        ? t(
                            "With no agents selected, all configured agents are allowed for new chats and workers.",
                          )
                        : allowedAgents.length
                          ? t(
                              "Agents can only use these configured agents for new chats and workers.",
                            )
                          : t(
                              "Agent creation is disabled because allowed_agents is empty in configuration. Select agents to allow them.",
                            )
                    }
                    agents={selectors.data?.agents ?? []}
                    models={selectors.data?.models ?? []}
                    value={allowedAgents}
                    onChange={(value) => {
                      let updated = updateDocument(
                        source,
                        allowedAgentsPath,
                        value,
                      );
                      // Clear the default agent when it is excluded by a non-empty allowlist.
                      const fallback = text(defaultAgentPath);
                      if (
                        fallback &&
                        value !== undefined &&
                        !value.includes(fallback)
                      )
                        updated = updateDocument(
                          updated,
                          defaultAgentPath,
                          undefined,
                        );
                      onChange(updated);
                    }}
                  />
                  <ResourceChoice
                    label={t("Default agent")}
                    description={t(
                      "Used when creating a new chat or worker without specifying an agent.",
                    )}
                    value={text(defaultAgentPath)}
                    loading={selectors.isPending || sources.isPending}
                    onValueChange={(value) =>
                      set(defaultAgentPath, value || undefined)
                    }
                    options={[
                      { value: "", label: t("Same as current agent") },
                      ...creationAgentOptions,
                    ]}
                  />
                  <ModelSelectionField
                    label={t("Allowed models")}
                    description={
                      allowedModels.length
                        ? t(
                            "Agents can use these models for new chats, workers, and subagents. Subagents can also use the current chat’s model.",
                          )
                        : t(
                            "Subagents can always use the current chat’s model. With no extra models selected, new chats and workers use their Agent’s model.",
                          )
                    }
                    models={selectors.data?.models ?? []}
                    value={allowedModels}
                    onChange={(value) => {
                      // The default must stay one of the allowed models; editing also migrates legacy subagents.dynamic keys.
                      const nextDefault =
                        defaultModel && value?.includes(defaultModel)
                          ? defaultModel
                          : undefined;
                      updateCreationModels(value, nextDefault);
                    }}
                  />
                  {allowedModels.length > 0 && (
                    <ResourceChoice
                      label={t("Default model")}
                      description={t(
                        "Used when the agent does not choose a model. With None, dynamic subagents use the current chat’s model.",
                      )}
                      value={defaultModel}
                      loading={selectors.isPending || sources.isPending}
                      onValueChange={(value) =>
                        updateCreationModels(allowedModels, value || undefined)
                      }
                      options={[
                        { value: "", label: t("None") },
                        ...models.filter((model) =>
                          allowedModels.includes(model.value),
                        ),
                      ]}
                    />
                  )}
                </SettingsSection>
                <SettingsSection
                  title={
                    <span className="flex items-center gap-1">
                      {t("Subagents")}
                      <HelpTip label={t("About subagents")}>
                        {t(
                          "Subagents run in the background inside the current chat. They do not add chats to your list. Follow them in Activity. Their results return to the main agent automatically.",
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
                      checked={dynamicSubagentsEnabled}
                      onCheckedChange={(enabled) =>
                        set(
                          ["subagents", "dynamic", "enabled"],
                          enabled || undefined,
                        )
                      }
                    />
                  </SettingsRow>
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
                <section id="tool-execution">
                  <SettingsSection
                    variant="grouped"
                    title={t("Tool execution")}
                  >
                    <ChoiceField
                      label={t("Automatic background execution")}
                      value={text(["tools", "execution", "mode"]) || "inline"}
                      options={[
                        { value: "inline", label: t("Shell only") },
                        { value: "auto", label: t("All eligible tools") },
                      ]}
                      onValueChange={(value) =>
                        set(["tools", "execution", "mode"], value)
                      }
                    />
                    <SettingsRow
                      label={t("Foreground wait (seconds)")}
                      controlId="tool-foreground-wait"
                      description={t(
                        "Tools still running after this wait continue in the background if eligible.",
                      )}
                    >
                      <Input
                        id="tool-foreground-wait"
                        type="number"
                        min={0}
                        step="any"
                        value={Number(
                          get([
                            "tools",
                            "execution",
                            "foreground_wait_seconds",
                          ]) ?? 5,
                        )}
                        onChange={(event) => {
                          const seconds = event.target.valueAsNumber;
                          if (Number.isFinite(seconds) && seconds >= 0)
                            set(
                              ["tools", "execution", "foreground_wait_seconds"],
                              seconds,
                            );
                        }}
                      />
                    </SettingsRow>
                  </SettingsSection>
                </section>
              </>
            )}
            {(!group || group === "bot") && (
              <BotFields
                source={source}
                onChange={onChange}
                showHeading={!group}
              />
            )}
            {(!group || group === "memory") && (
              <>
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
                            get(["memory", "auto_organize", "enabled"]) !==
                            false
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
              </>
            )}
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
