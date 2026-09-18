import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  ReadOnlyField,
  Input,
} from "a13n-ui";

import { SearchPicker } from "a13n-ui";

import { ApiError } from "../../service-client";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { EditorSection } from "./section";

import { ArrowLeftIcon, CheckIcon, CircleIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { ErrorNotice, ErrorToast } from "../../shared/feedback";
import { TextAreaField } from "../../shared/form";
import { ResourceReference } from "../../shared/resource-reference";
import { jsonObject, validateSettings } from "../../shared/validation";
import styles from "./agents.module.css";
import { AgentMemorySelection } from "../memory/selection";
import { AgentAvatar } from "./avatar";
import { AgentCapabilities } from "./capabilities";
import { AgentToolsets } from "./toolsets";
import { useAgentChoices } from "./choices";
import { ModelIcon } from "../models/model-icon";
import { useModelProviderDefinitions } from "../models/provider-definitions";
import { advancedConfig, buildConfig, type AgentConfig } from "./configuration";
import { AgentEnvironment } from "./environment";

function thinkingSelection(value: unknown): string {
  if (value === false) return "false";
  if (typeof value === "string") return value;
  return "true";
}

export function AgentForm({
  initial: providedInitial,
  version,
  etag,
  creating = false,
  name: initialName = "",
  description: initialDescription = "",
  pending,
  error,
  submit,
  reload,
  readonly = false,
  context,
  primaryAction,
  identityAction,
  imageUrl,
  agentId,
  agentKey,
  imagePicker,
  metadata,
  back,
}: {
  initial: AgentConfig;
  version?: number;
  etag?: string;
  creating?: boolean;
  name?: string;
  description?: string;
  pending: boolean;
  error: unknown;
  submit: (
    config: AgentConfig,
    name: string,
    description: string,
    etag?: string,
    changeSummary?: string | null,
  ) => void;
  reload?: () => void;
  readonly?: boolean;
  context?: ReactNode;
  primaryAction?: ReactNode;
  identityAction?: ReactNode;
  imageUrl?: string | null;
  agentId?: string;
  agentKey?: string;
  imagePicker?: (name: string) => ReactNode;
  metadata?: ReactNode;
  back: string;
}) {
  const [initial] = useState(providedInitial),
    [originalVersion] = useState(version),
    [originalEtag] = useState(etag);
  const { t } = useTranslation();
  const initialSettings = initial.model.settings ?? {};
  const {
    thinking: initialThinking,
    max_tokens: initialMaxTokens,
    ...initialExtraSettings
  } = initialSettings;
  const [name, setName] = useState(initialName),
    [description, setDescription] = useState(initialDescription),
    [environmentTemplateId, setEnvironmentTemplateId] = useState(
      initial.default_environment_template_id ?? null,
    ),
    [changeSummary, setChangeSummary] = useState(""),
    [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model.model_key),
    [thinking, setThinking] = useState(thinkingSelection(initialThinking)),
    [maxTokens, setMaxTokens] = useState(
      typeof initialMaxTokens === "number" ? String(initialMaxTokens) : "",
    ),
    [settings, setSettings] = useState(
      JSON.stringify(initialExtraSettings, null, 2),
    ),
    [advanced, setAdvanced] = useState(advancedConfig(initial)),
    [expanded, setExpanded] = useState(false),
    [modelExpanded, setModelExpanded] = useState(false),
    [validation, setValidation] = useState<Error>(),
    [modelValidation, setModelValidation] = useState<Error>();
  const [toolsets, setToolsets] = useState(initial.toolsets ?? {});
  const [memory, setMemory] = useState(initial.memory);
  const [skills, setSkills] = useState(initial.skills ?? []),
    [connections, setConnections] = useState(initial.connection_tools ?? []);
  const choices = useAgentChoices();
  const definitions = useModelProviderDefinitions();
  const selectedModel = choices.data?.models.find((item) => item.key === model);
  const settingsSchema = definitions.data?.items.find(
    (definition) =>
      selectedModel?.model_api &&
      definition.settings_schemas[selectedModel.model_api],
  )?.settings_schemas[selectedModel?.model_api ?? ""];
  const thinkingSchema = settingsSchema?.properties as
    Record<string, unknown> | undefined;
  const thinkingVariants =
    (thinkingSchema?.thinking as { anyOf?: { enum?: unknown[] }[] } | undefined)
      ?.anyOf ?? [];
  const thinkingEfforts = thinkingVariants
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
  useEffect(() => {
    if (error instanceof ApiError && [400, 422].includes(error.status)) {
      setExpanded(true);
      setModelExpanded(true);
    }
  }, [error]);
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
      if (creating && !config.protocol.public_name)
        config.protocol.public_name = name;
      setValidation(undefined);
      submit(
        config,
        name,
        description,
        originalEtag,
        changeSummary.trim() || null,
      );
    } catch (error) {
      setValidation(
        error instanceof Error ? error : new Error(t("Invalid configuration")),
      );
      setExpanded(true);
    }
  }
  const dirty =
    creating ||
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
  return (
    <div className={styles.editorPage}>
      <Link className={styles.back} to={back}>
        <ArrowLeftIcon size={14} />
        {t("Agents")}
      </Link>
      <form onSubmit={save} className={styles.editor}>
        <header className={styles.identity}>
          <div className={styles.identityHeading}>
            <AgentAvatar
              name={name}
              id={agentId}
              url={imageUrl}
              className={`${styles.agentIcon} text-base`}
            />
            <h1>{creating ? t("Create agent") : initialName}</h1>
            {agentId && (
              <ResourceReference id={agentId} resourceKey={agentKey} />
            )}
            {identityAction && (
              <fieldset
                disabled={pending || dirty}
                className="fieldset-reset"
                title={
                  dirty
                    ? t("Save or discard changes before managing this agent.")
                    : undefined
                }
              >
                {identityAction}
              </fieldset>
            )}
          </div>
          {(creating || initialDescription) && (
            <p>
              {creating
                ? t("Start with clear instructions and the right model.")
                : initialDescription}
            </p>
          )}
          <div className={styles.headerActions}>
            <fieldset disabled={pending || dirty} className="fieldset-reset">
              {primaryAction}
            </fieldset>
            {dirty && primaryAction && (
              <small>{t("Save changes before trying this agent.")}</small>
            )}
          </div>
          <div className={styles.headerDetails}>
            <div className={styles.headerMeta}>
              {originalVersion !== undefined && (
                <span>
                  {t("Default version")} <strong>v{originalVersion}</strong>
                </span>
              )}
              {metadata}
            </div>
            <div className={styles.historyActions}>
              <fieldset
                disabled={pending || dirty}
                className="fieldset-reset"
                title={
                  dirty
                    ? t("Save or discard changes before managing this agent.")
                    : undefined
                }
              >
                {context}
              </fieldset>
            </div>
          </div>
        </header>
        <div className={styles.main}>
          <fieldset disabled={pending} className="fieldset-reset">
            <EditorSection
              title={t(creating ? "General" : "Model")}
              description={t(
                creating
                  ? "The essentials: identity and the model behind this agent."
                  : "Choose the model that powers this agent.",
              )}
            >
              {creating && (
                <section className={styles.section}>
                  {imagePicker?.(name)}
                  <FormField className="min-w-0 w-full" label={t("Agent name")}>
                    <Input
                      required={true}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      maxLength={128}
                    />
                  </FormField>
                  <FormField
                    className="min-w-0 w-full"
                    label={t("Description")}
                  >
                    <Input
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      maxLength={4096}
                    />
                  </FormField>
                </section>
              )}
              <div className={styles.modelProperty}>
                {creating && (
                  <span className={styles.fieldLabel}>{t("Model")}</span>
                )}
                {readonly ? (
                  <ReadOnlyField label={t("Model")}>
                    {selectedModel ? (
                      <span className="inline-flex items-center gap-2">
                        <ModelIcon
                          upstream={selectedModel.upstream_model}
                          catalogRef={selectedModel.catalog_ref}
                          size={20}
                        />
                        {selectedModel.name}
                      </span>
                    ) : (
                      model
                    )}
                  </ReadOnlyField>
                ) : (
                  <SearchPicker
                    label={t("Model")}
                    placeholder={t("Choose a model…")}
                    emptyMessage={t(
                      "No models available. Configure a provider and model first.",
                    )}
                    value={model}
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
                            description: [
                              ...new Set([item.key, item.upstream_model]),
                            ]
                              .filter((value) => value !== item.name)
                              .join(" · "),
                          })) ?? [],
                      },
                    ]}
                    onValueChange={setModel}
                  />
                )}
              </div>
              <div className={styles.modelSettings}>
                <div className={styles.modelSettingsFields}>
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
                  title={t("Additional model settings")}
                  summary={
                    Object.keys(initialExtraSettings).length ||
                    settings.trim() !== "{}"
                      ? t("JSON")
                      : undefined
                  }
                  open={modelExpanded}
                  onOpenChange={setModelExpanded}
                >
                  <TextAreaField
                    readOnly={readonly}
                    label={t("Additional model settings")}
                    hideLabel
                    hint={t(
                      "Optional JSON settings override the selected model's defaults.",
                    )}
                    code
                    value={settings}
                    onChange={setSettings}
                    rows={5}
                  />
                  <ErrorNotice error={modelValidation} />
                </DisclosureSection>
              </div>
            </EditorSection>
          </fieldset>
          <AgentEnvironment
            value={environmentTemplateId}
            onChange={setEnvironmentTemplateId}
            disabled={readonly || pending}
          />
          <fieldset
            disabled={pending}
            className={`fieldset-reset ${styles.configurationSections}`}
          >
            <EditorSection
              title={t("Instructions")}
              description={t(
                "The role, boundaries, and approach for this agent.",
              )}
            >
              <div className={styles.instructions}>
                <TextAreaField
                  readOnly={readonly}
                  label={t("System instructions")}
                  hideLabel
                  value={instructions}
                  onChange={setInstructions}
                  rows={8}
                />
              </div>
            </EditorSection>
            <AgentCapabilities
              readOnly={readonly}
              choices={choices}
              skills={skills}
              setSkills={setSkills}
              connections={connections}
              setConnections={setConnections}
            />
            <AgentToolsets
              value={toolsets}
              onChange={setToolsets}
              reviewer={initial.reviewer}
              readOnly={readonly}
            />
            <EditorSection
              title={t("Memory")}
              description={t(
                "Remember useful information across runs, with explicit control over what is stored.",
              )}
            >
              <AgentMemorySelection
                agentId={agentId}
                savedProviderId={
                  providedInitial.memory &&
                  "provider_id" in providedInitial.memory
                    ? providedInitial.memory.provider_id
                    : undefined
                }
                readOnly={readonly}
                value={memory}
                onChange={setMemory}
              />
            </EditorSection>
            <EditorSection
              title={t("Advanced configuration")}
              description={t(
                "Fine-tune how this agent runs and returns results.",
              )}
            >
              <DisclosureSection
                open={expanded}
                onOpenChange={setExpanded}
                title={t(readonly ? "Configuration" : "Edit configuration")}
              >
                <div>
                  <TextAreaField
                    readOnly={readonly}
                    label={t("Configuration JSON")}
                    hint={t(
                      "Input adapter, protocol, structured output, retries, subagents, and client tools.",
                    )}
                    code
                    value={advanced}
                    onChange={setAdvanced}
                    rows={18}
                  />
                  <ErrorNotice error={validation} />
                </div>
              </DisclosureSection>
            </EditorSection>
            {!creating && !readonly && (
              <EditorSection
                title={t("Version note")}
                description={t(
                  "Optional explanation saved with the new version.",
                )}
              >
                <TextAreaField
                  label={t("Version note")}
                  hideLabel
                  value={changeSummary}
                  onChange={setChangeSummary}
                  rows={2}
                />
              </EditorSection>
            )}
          </fieldset>
        </div>

        <aside className={styles.saveBar} aria-label={t("Agent actions")}>
          <div className={styles.savePanel}>
            <span className={styles.saveStatus} role="status">
              {dirty ? <CircleIcon size={12} /> : <CheckIcon size={14} />}{" "}
              {t(dirty ? "Unsaved changes" : "All changes saved")}
            </span>
            {!readonly && dirty && (
              <Button
                type="submit"
                variant="default"
                disabled={
                  pending || !dirty || !model || (creating && !name.trim())
                }
                loading={pending}
              >
                {t(creating ? "Create agent" : "Save changes")}
              </Button>
            )}
            {reload && dirty && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={reload}
              >
                {t("Discard changes")}
              </Button>
            )}
          </div>
        </aside>
        <ErrorToast error={error ?? choices.error} />
      </form>
    </div>
  );
}
