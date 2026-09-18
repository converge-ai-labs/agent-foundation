import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
} from "a13n-ui";
import { PuzzlePieceIcon, XIcon } from "@phosphor-icons/react";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { ApiError } from "../../../service-client";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice, ErrorToast } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/form";
import { jsonObject, validateSettings } from "../../../shared/validation";
import { useMemoryProviders } from "../../memory/availability";
import { AgentMemorySelection } from "../../memory/selection";
import { ModelIcon } from "../../models/model-icon";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import { useAgentChoices } from "../choices";
import {
  advancedConfig,
  buildConfig,
  type AgentConfig,
} from "../configuration";
import {
  ConnectionBrandIcon,
  ConnectionGroup,
  displayName,
} from "../connections";
import { AgentEnvironment } from "../environment";
import { EditorLayoutContext, EditorSection } from "../section";
import { AgentToolsets } from "../toolsets";
import agentStyles from "../agents.module.css";
import styles from "./next.module.css";
import { ResourcePicker } from "./picker";

function thinkingSelection(value: unknown): string {
  if (value === false) return "false";
  if (typeof value === "string") return value;
  return "true";
}

export interface AgentDraftSummary {
  modelName?: string;
  modelIcon?: ReactNode;
  environmentId: string | null;
  skillCount: number;
  connectionCount: number;
  dirty: boolean;
}

/**
 * Configuration editor for an existing agent. Saving publishes a new immutable
 * version; the sticky bar makes that explicit instead of implying autosave.
 */
export function AgentEditor({
  agentId,
  initial,
  version,
  etag,
  pending,
  error,
  readonly,
  submit,
  discard,
  rail,
}: {
  agentId: string;
  initial: AgentConfig;
  version: number;
  etag?: string;
  pending: boolean;
  error: unknown;
  readonly: boolean;
  submit: (config: AgentConfig, etag?: string, note?: string | null) => void;
  discard: () => void;
  rail: (summary: AgentDraftSummary) => ReactNode;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const initialSettings = initial.model.settings ?? {};
  const {
    thinking: initialThinking,
    max_tokens: initialMaxTokens,
    ...initialExtraSettings
  } = initialSettings;
  const [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model.model_key),
    [thinking, setThinking] = useState(thinkingSelection(initialThinking)),
    [maxTokens, setMaxTokens] = useState(
      typeof initialMaxTokens === "number" ? String(initialMaxTokens) : "",
    ),
    [settings, setSettings] = useState(
      JSON.stringify(initialExtraSettings, null, 2),
    ),
    [advanced, setAdvanced] = useState(advancedConfig(initial)),
    [environmentTemplateId, setEnvironmentTemplateId] = useState(
      initial.default_environment_template_id ?? null,
    ),
    [toolsets, setToolsets] = useState(initial.toolsets ?? {}),
    [memory, setMemory] = useState(initial.memory),
    [skills, setSkills] = useState(initial.skills ?? []),
    [connections, setConnections] = useState(initial.connection_tools ?? []),
    [note, setNote] = useState(""),
    [modelExpanded, setModelExpanded] = useState(false),
    [advancedExpanded, setAdvancedExpanded] = useState(false),
    [validation, setValidation] = useState<Error>(),
    [modelValidation, setModelValidation] = useState<Error>();
  const choices = useAgentChoices();
  const definitions = useModelProviderDefinitions();
  const { visible: memoryVisible } = useMemoryProviders();
  const selectedModel = choices.data?.models.find((item) => item.key === model);
  const settingsSchema = definitions.data?.items.find(
    (definition) =>
      selectedModel?.model_api &&
      definition.settings_schemas[selectedModel.model_api],
  )?.settings_schemas[selectedModel?.model_api ?? ""];
  const thinkingEfforts = (
    (
      (settingsSchema?.properties as Record<string, unknown> | undefined)
        ?.thinking as { anyOf?: { enum?: unknown[] }[] } | undefined
    )?.anyOf ?? []
  )
    .flatMap((variant) => variant.enum ?? [])
    .filter((value): value is string => typeof value === "string");
  const thinkingOptions = [
    { value: "true", label: t("On (default effort)") },
    { value: "false", label: t("Off") },
    ...thinkingEfforts.map((value) => ({
      value,
      label: t(value.charAt(0).toUpperCase() + value.slice(1)),
    })),
    ...(thinking && !["true", "false", ...thinkingEfforts].includes(thinking)
      ? [{ value: thinking, label: thinking }]
      : []),
  ];
  const dirty =
    JSON.stringify(memory) !== JSON.stringify(initial.memory) ||
    environmentTemplateId !==
      (initial.default_environment_template_id ?? null) ||
    JSON.stringify(toolsets) !== JSON.stringify(initial.toolsets ?? {}) ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model.model_key ||
    thinking !== thinkingSelection(initialThinking) ||
    maxTokens !==
      (typeof initialMaxTokens === "number" ? String(initialMaxTokens) : "") ||
    settings !== JSON.stringify(initialExtraSettings, null, 2) ||
    advanced !== advancedConfig(initial) ||
    JSON.stringify(skills) !== JSON.stringify(initial.skills ?? []) ||
    JSON.stringify(connections) !==
      JSON.stringify(initial.connection_tools ?? []);
  const invalidResponse =
    error instanceof ApiError && [400, 422].includes(error.status);

  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    let modelSettings: ReturnType<typeof jsonObject>;
    try {
      const extraSettings = jsonObject(settings);
      if ("thinking" in extraSettings || "max_tokens" in extraSettings)
        throw new Error(
          t("Edit thinking and max output tokens using their fields above."),
        );
      modelSettings = {
        ...extraSettings,
        thinking:
          thinking === "true" ? true : thinking === "false" ? false : thinking,
        ...(maxTokens ? { max_tokens: Number(maxTokens) } : {}),
      };
      if (settingsSchema) validateSettings(settingsSchema, modelSettings);
      setModelValidation(undefined);
    } catch (error) {
      setModelValidation(
        error instanceof Error ? error : new Error(t("Invalid configuration")),
      );
      setModelExpanded(true);
      return;
    }
    try {
      const config = buildConfig(
        initial,
        {
          instructions,
          memory,
          toolsets,
          reviewer: initial.reviewer,
          model: {
            ...initial.model,
            model_key: model,
            settings: modelSettings,
          },
          skills,
          connection_tools: connections,
          default_environment_template_id: environmentTemplateId,
        },
        advanced,
      );
      setValidation(undefined);
      submit(config, etag, note.trim() || null);
    } catch (error) {
      setValidation(
        error instanceof Error ? error : new Error(t("Invalid configuration")),
      );
      setAdvancedExpanded(true);
    }
  }

  const skillItems = choices.data?.skills ?? [];
  const availableConnections = choices.data?.connections ?? [];
  const selectedSkillKeys = new Set(skills.map((item) => item.skill_key));
  const selectedConnectionIds = new Set(
    connections.map((item) => item.connection_id),
  );
  const nextVersion = version + 1;

  return (
    <EditorLayoutContext.Provider
      value={{ card: true, permissionLabels: true }}
    >
      <form onSubmit={save} className={styles.layout}>
        <fieldset
          disabled={pending}
          className={`fieldset-reset ${styles.main}`}
        >
          <EditorSection
            title={t("Model")}
            description={t(
              "The model that powers this agent and how it reasons.",
            )}
          >
            <SearchPicker
              label={t("Model")}
              placeholder={t("Choose a model…")}
              emptyMessage={t(
                "No models available. Configure a provider and model first.",
              )}
              value={model}
              disabled={readonly}
              groups={[
                {
                  label: t("Available models"),
                  options:
                    choices.data?.models.map((item) => ({
                      value: item.key,
                      label: item.name,
                      icon: (
                        <ModelIcon
                          upstream={item.upstream_model}
                          catalogRef={item.catalog_ref}
                          size={20}
                        />
                      ),
                      description: [...new Set([item.key, item.upstream_model])]
                        .filter((value) => value !== item.name)
                        .join(" · "),
                    })) ?? [],
                },
              ]}
              onValueChange={setModel}
            />
            <div className={styles.modelGrid}>
              <ChoiceField
                label={t("Thinking effort")}
                readOnly={readonly}
                value={thinking}
                onValueChange={setThinking}
                options={thinkingOptions}
              />
              <FormField label={t("Max output tokens")} readOnly={readonly}>
                <Input
                  type="number"
                  min={1}
                  step={1}
                  placeholder={t("Model default")}
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value)}
                />
              </FormField>
            </div>
            <DisclosureSection
              title={t("Provider-specific settings")}
              summary={
                Object.keys(initialExtraSettings).length ||
                settings.trim() !== "{}"
                  ? t("Customized")
                  : t("Defaults")
              }
              open={modelExpanded || (invalidResponse && !!modelValidation)}
              onOpenChange={setModelExpanded}
            >
              <TextAreaField
                readOnly={readonly}
                label={t("Provider-specific settings")}
                hideLabel
                hint={t(
                  "JSON passed to the provider on top of the model defaults.",
                )}
                code
                value={settings}
                onChange={setSettings}
                rows={5}
              />
              <ErrorNotice error={modelValidation} />
            </DisclosureSection>
          </EditorSection>

          <EditorSection
            title={t("Instructions")}
            description={t(
              "The role, boundaries, and approach the agent follows on every run.",
            )}
          >
            <div className={agentStyles.instructions}>
              <TextAreaField
                readOnly={readonly}
                label={t("System instructions")}
                hideLabel
                value={instructions}
                onChange={setInstructions}
                rows={10}
              />
            </div>
            <div className={styles.instructionsMeta}>
              <span>{t("Markdown is supported.")}</span>
              <span>
                {t("{{count}} characters", { count: instructions.length })}
              </span>
            </div>
          </EditorSection>

          <EditorSection
            title={t("Skills")}
            description={t(
              "Reusable knowledge and procedures the agent can load.",
            )}
            actions={
              !readonly && (
                <ResourcePicker
                  label={t("Add skill")}
                  searchLabel={t("Search skills")}
                  emptyLabel={t("No skills in this workspace yet.")}
                  loading={choices.isPending}
                  manageHref={`${basePath}/skills`}
                  manageLabel={t("Manage skills")}
                  items={skillItems.map((skill) => ({
                    id: skill.key,
                    name: skill.name,
                    detail: skill.key,
                  }))}
                  selected={selectedSkillKeys}
                  onToggle={(key, checked) =>
                    setSkills((previous) =>
                      checked
                        ? [...previous, { skill_key: key }]
                        : previous.filter((item) => item.skill_key !== key),
                    )
                  }
                />
              )
            }
          >
            {skills.length ? (
              <div className={styles.list}>
                {skills.map((selection) => {
                  const skill = skillItems.find(
                    (item) => item.key === selection.skill_key,
                  );
                  const versions = Array.from(
                    { length: skill?.version ?? 0 },
                    (_, index) => skill!.version - index,
                  );
                  return (
                    <div className={styles.listRow} key={selection.skill_key}>
                      <span className={styles.listIcon} aria-hidden="true">
                        <PuzzlePieceIcon size={16} />
                      </span>
                      <span className={styles.listCopy}>
                        <strong>{skill?.name ?? selection.skill_key}</strong>
                        <small>
                          {skill ? skill.key : t("Not in this workspace")}
                        </small>
                      </span>
                      <label className={styles.listControl}>
                        {t("Version")}
                        <select
                          disabled={readonly}
                          value={selection.version ?? ""}
                          onChange={(event) =>
                            setSkills((previous) =>
                              previous.map((item) =>
                                item.skill_key === selection.skill_key
                                  ? {
                                      ...item,
                                      version: event.target.value
                                        ? Number(event.target.value)
                                        : null,
                                    }
                                  : item,
                              ),
                            )
                          }
                        >
                          <option value="">{t("Latest")}</option>
                          {versions.map((value) => (
                            <option key={value} value={value}>
                              v{value}
                            </option>
                          ))}
                          {selection.version &&
                            !versions.includes(selection.version) && (
                              <option value={selection.version}>
                                v{selection.version}
                              </option>
                            )}
                        </select>
                      </label>
                      {!readonly && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("Remove {{name}}", {
                            name: skill?.name ?? selection.skill_key,
                          })}
                          onClick={() =>
                            setSkills((previous) =>
                              previous.filter(
                                (item) =>
                                  item.skill_key !== selection.skill_key,
                              ),
                            )
                          }
                        >
                          <XIcon />
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className={styles.emptyList}>
                <span>
                  {t(
                    "No skills attached. The agent relies on its instructions alone.",
                  )}
                </span>
              </p>
            )}
          </EditorSection>

          <EditorSection
            title={t("Connections")}
            description={t(
              "External services and MCP servers whose tools the agent may call.",
            )}
            actions={
              !readonly && (
                <ResourcePicker
                  label={t("Add connection")}
                  searchLabel={t("Search connections")}
                  emptyLabel={t("No connections in this workspace yet.")}
                  loading={choices.isPending}
                  manageHref={`${basePath}/connections`}
                  manageLabel={t("Manage connections")}
                  items={availableConnections.map((connection) => ({
                    id: connection.id,
                    name: displayName(connection),
                    detail:
                      connection.source.kind === "mcp"
                        ? connection.source.endpoint_url
                        : connection.source.connector_key,
                    icon: <ConnectionBrandIcon connection={connection} />,
                    disabled: connection.status !== "ready",
                    disabledReason: t(connection.status),
                  }))}
                  selected={selectedConnectionIds}
                  onToggle={(id, checked) =>
                    setConnections((previous) =>
                      checked
                        ? [
                            ...previous,
                            {
                              connection_id: id,
                              tools: null,
                              defer_loading: true,
                            },
                          ]
                        : previous.filter((item) => item.connection_id !== id),
                    )
                  }
                />
              )
            }
          >
            {connections.length ? (
              <div className={agentStyles.toolsetCard}>
                {connections.map((selection) => (
                  <ConnectionGroup
                    key={selection.connection_id}
                    connection={availableConnections.find(
                      (item) => item.id === selection.connection_id,
                    )}
                    selection={selection}
                    readOnly={readonly}
                    onChange={(change) =>
                      setConnections((previous) =>
                        previous.map((item) =>
                          item.connection_id === selection.connection_id
                            ? change(item)
                            : item,
                        ),
                      )
                    }
                    onRemove={() =>
                      setConnections((previous) =>
                        previous.filter(
                          (item) =>
                            item.connection_id !== selection.connection_id,
                        ),
                      )
                    }
                  />
                ))}
              </div>
            ) : (
              <p className={styles.emptyList}>
                <span>{t("No connections attached.")}</span>
                <Link to={`${basePath}/connections`}>
                  {t("Manage connections")}
                </Link>
              </p>
            )}
          </EditorSection>

          <AgentToolsets
            value={toolsets}
            onChange={setToolsets}
            reviewer={initial.reviewer}
            readOnly={readonly}
          />

          <AgentEnvironment
            value={environmentTemplateId}
            onChange={setEnvironmentTemplateId}
            disabled={readonly || pending}
          />

          {(memoryVisible || memory || initial.memory) && (
            <EditorSection
              title={t("Memory")}
              description={t(
                "Remember useful information across runs, with explicit control over what is stored.",
              )}
            >
              <AgentMemorySelection
                agentId={agentId}
                savedProviderId={initial.memory?.provider_id}
                readOnly={readonly}
                value={memory}
                onChange={setMemory}
              />
            </EditorSection>
          )}

          <EditorSection
            title={t("Advanced configuration")}
            description={t(
              "Protocol, input adapter, structured output, retries, and subagents.",
            )}
          >
            <DisclosureSection
              open={advancedExpanded || (invalidResponse && !!validation)}
              onOpenChange={setAdvancedExpanded}
              title={t(readonly ? "Configuration" : "Edit configuration JSON")}
            >
              <TextAreaField
                readOnly={readonly}
                label={t("Configuration JSON")}
                hideLabel
                code
                value={advanced}
                onChange={setAdvanced}
                rows={18}
              />
              <ErrorNotice error={validation} />
            </DisclosureSection>
          </EditorSection>
        </fieldset>

        <aside className={styles.rail}>
          {rail({
            modelName: selectedModel?.name ?? model,
            modelIcon: selectedModel && (
              <ModelIcon
                upstream={selectedModel.upstream_model}
                catalogRef={selectedModel.catalog_ref}
                size={16}
              />
            ),
            environmentId: environmentTemplateId,
            skillCount: skills.length,
            connectionCount: connections.length,
            dirty,
          })}
        </aside>

        {dirty && !readonly && (
          <div
            className={styles.saveBar}
            role="region"
            aria-label={t("Unsaved changes")}
          >
            <div className={styles.saveStatus}>
              <span className={styles.savePulse} aria-hidden="true" />
              <span>
                {t("Unsaved changes")}
                <small>
                  {t(
                    "Saving publishes v{{version}} and makes it the default.",
                    {
                      version: nextVersion,
                    },
                  )}
                </small>
              </span>
            </div>
            <div className={styles.saveNote}>
              <Input
                size="sm"
                aria-label={t("Version note")}
                placeholder={t("Version note (optional)")}
                value={note}
                maxLength={280}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            <div className={styles.saveActions}>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={discard}
              >
                {t("Discard")}
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={pending || !model}
                loading={pending}
              >
                {t("Save as v{{version}}", { version: nextVersion })}
              </Button>
            </div>
          </div>
        )}
        <ErrorToast error={error ?? choices.error} />
      </form>
    </EditorLayoutContext.Provider>
  );
}
