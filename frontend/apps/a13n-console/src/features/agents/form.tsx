import {
  Button,
  DisclosureSection,
  FormField,
  ReadOnlyField,
  Input,
} from "a13n-ui";

import { SearchPicker } from "a13n-ui";

import { ApiError } from "@converge.ai/a13n";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { EditorSection } from "./section";

import {
  ArrowLeftIcon,
  CheckIcon,
  CircleIcon,
  StackIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { ErrorNotice, ErrorToast } from "../../shared/feedback";
import { TextAreaField } from "../../shared/form";
import { ResourceReference } from "../../shared/resource-reference";
import { jsonObject } from "../../shared/validation";
import styles from "./agents.module.css";
import { AgentSearchSelection } from "../web/selection";
import { AgentAvatar } from "./avatar";
import { AgentCapabilities } from "./capabilities";
import { useAgentChoices } from "./choices";
import {
  advancedConfig,
  buildConfig,
  searchSelection,
  type AgentConfig,
  withSearchSelection,
} from "./configuration";

export function AgentForm({
  initial: providedInitial,
  version,
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
  environment,
  metadata,
  back,
}: {
  initial: AgentConfig;
  version?: number;
  creating?: boolean;
  name?: string;
  description?: string;
  pending: boolean;
  error: unknown;
  submit: (
    config: AgentConfig,
    name: string,
    description: string,
    version?: number,
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
  environment?: ReactNode;
  metadata?: ReactNode;
  back: string;
}) {
  const [initial] = useState(providedInitial),
    [originalVersion] = useState(version);
  const { t } = useTranslation();
  const [name, setName] = useState(initialName),
    [description, setDescription] = useState(initialDescription),
    [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model.model_key),
    [settings, setSettings] = useState(
      JSON.stringify(initial.model.settings ?? {}, null, 2),
    ),
    [advanced, setAdvanced] = useState(advancedConfig(initial)),
    [expanded, setExpanded] = useState(false),
    [modelExpanded, setModelExpanded] = useState(false),
    [validation, setValidation] = useState<Error>();
  const [search, setSearch] = useState(searchSelection(initial));
  const [skills, setSkills] = useState(initial.skills ?? []),
    [connections, setConnections] = useState(initial.connection_tools ?? []);
  const choices = useAgentChoices();
  useEffect(() => {
    if (error instanceof ApiError && [400, 422].includes(error.status)) {
      setExpanded(true);
      setModelExpanded(true);
    }
  }, [error]);
  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    try {
      const config = buildConfig(
        initial,
        {
          instructions,
          toolsets: withSearchSelection(initial.toolsets, search),
          model: {
            ...initial.model,
            model_key: model,
            settings: jsonObject(settings),
          },
          skills,
          connection_tools: connections,
        },
        advanced,
      );
      if (creating && !config.protocol.public_name)
        config.protocol.public_name = name;
      setValidation(undefined);
      submit(config, name, description, originalVersion);
    } catch (error) {
      setValidation(
        error instanceof Error ? error : new Error(t("Invalid configuration")),
      );
      setExpanded(true);
      setModelExpanded(true);
    }
  }
  const dirty =
    creating ||
    JSON.stringify(search) !== JSON.stringify(searchSelection(initial)) ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model.model_key ||
    settings !== JSON.stringify(initial.model.settings ?? {}, null, 2) ||
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
              {version !== undefined && (
                <span>
                  {t("Current version")} <strong>v{version}</strong>
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
                    {choices.data?.models.find((item) => item.key === model)
                      ?.name ?? model}
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
                            icon: <StackIcon size={14} />,
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
              <DisclosureSection
                title={t("Model settings")}
                summary={t(settings.trim() === "{}" ? "Default" : "Custom")}
                open={modelExpanded}
                onOpenChange={setModelExpanded}
              >
                <TextAreaField
                  readOnly={readonly}
                  label={t("Model settings")}
                  hideLabel
                  hint={t(
                    "Settings override the selected model's defaults. Use a JSON object.",
                  )}
                  code
                  value={settings}
                  onChange={setSettings}
                  rows={4}
                />
              </DisclosureSection>
            </EditorSection>
          </fieldset>
          {environment}
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
            <EditorSection
              title={t("Web search")}
              description={t(
                "Search the web and read pages with a connected account.",
              )}
            >
              <AgentSearchSelection
                readOnly={readonly}
                value={search}
                onChange={setSearch}
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
