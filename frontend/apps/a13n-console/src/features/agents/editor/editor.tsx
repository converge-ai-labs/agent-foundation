import { Badge } from "a13n-ui";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import { ExecutionFields } from "../../../shared/forms";
import { ErrorToast } from "../../../shared/feedback";
import {
  DetailLayout,
  SaveBar,
  Section,
  SectionGroup,
  Sections,
} from "../../../shared/page";
import { ModelIcon, providerBrand } from "../../models/model-icon";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import { useAgentChoices } from "../choices";
import {
  sectionsOf,
  type AgentConfig,
  type EditorSection,
} from "../configuration";
import { AgentEnvironment } from "../environment";
import { AgentToolsets } from "../toolsets";
import { AdvancedSection } from "./advanced";
import {
  ConnectionsSection,
  SkillsSection,
  MediaUnderstandingSection,
} from "./capabilities";
import { buildDraftConfig, thinkingEfforts, useAgentDraft } from "./draft";
import { InstructionsSection } from "./instructions";
import { MemorySection } from "./memory";
import { ReviewerSection, ReviewerSettings } from "./reviewer";
import type { ReviewTarget } from "../reviewer";
import { ConfigurationGroup } from "./configuration-group";
import { ModelSection, ModelSettings } from "./model";
import styles from "./editor.module.css";
import { ToolSearchSection } from "./tool-search";

export interface AgentDraftSummary {
  modelName?: string;
  modelIcon?: ReactNode;
  environmentId: string | null;
  skillCount: number;
  connectionCount: number;
  dirty: boolean;
}

/**
 * One editor serves creation and configuration. Creating starts from a draft
 * with no existing version, so saving creates the agent; editing an existing
 * agent publishes a new immutable version.
 */
export function AgentEditor({
  id,
  agentId,
  profile,
  initial: incomingInitial,
  version: incomingVersion,
  etag: incomingEtag,
  pending,
  error,
  readonly = false,
  submit,
  discard,
  rail,
  saveLabel,
  unnamed = false,
  onUnnamed,
  overridden,
  preset: incomingPreset,
}: {
  /** Lets a submit button outside the form, such as a page header's, save. */
  id?: string;
  /** The agent a save publishes a version of; none while creating. */
  agentId?: string;
  /** Creation's name and avatar, the first row of the form. */
  profile?: ReactNode;
  initial: AgentConfig;
  /** Omitted while creating: there is no published version yet. */
  version?: number;
  etag?: string;
  pending: boolean;
  error: unknown;
  readonly?: boolean;
  submit: (config: AgentConfig, etag?: string, note?: string | null) => void;
  /** A managed preset edits supplements; the Host supplies its official guidance. */
  preset?: Schema["AgentRevision"];
  discard?: () => void;
  rail?: (summary: AgentDraftSummary) => ReactNode;
  saveLabel?: string;
  /** Creation waits for the name its profile holds. */
  unnamed?: boolean;
  /** A save was refused for the missing name; the owner points at it. */
  onUnnamed?: () => void;
  /** Fields a read-only view's configuration changes from its version. */
  overridden?: Iterable<string>;
}) {
  const { t } = useTranslation();
  // Keep the draft, base revision and precondition together across background query refreshes.
  // An explicit reload/remount starts a new edit.
  const [{ initial, version, etag, preset }] = useState({
    initial: incomingInitial,
    version: incomingVersion,
    etag: incomingEtag,
    preset: incomingPreset,
  });
  const creating = version === undefined;
  const draft = useAgentDraft(initial);
  const configurationReadonly = readonly || !!preset;
  const [note, setNote] = useState(""),
    [modelExpanded, setModelExpanded] = useState(false),
    [attempted, setAttempted] = useState(false),
    [validation, setValidation] = useState<Error>(),
    [modelValidation, setModelValidation] = useState<Error>(),
    [toolSearchValidation, setToolSearchValidation] = useState<Error>();
  const [reviewPanel, setReviewPanel] = useState<{
    target?: ReviewTarget;
    validation?: Error;
  }>();
  const choices = useAgentChoices();
  const modelId = useId();
  const [advancedExpanded, setAdvancedExpanded] = useState(readonly);
  const definitions = useModelProviderDefinitions();
  const selectedModel = choices.data?.models.find(
    (item) => item.key === draft.model,
  );
  const selectedProvider = choices.data?.providers.find(
    (item) => item.id === selectedModel?.provider_id,
  );
  const modelApi = selectedModel?.config.model_api;
  const settingsSchema = modelApi
    ? definitions.data?.items
        .map((definition) => definition.settings_schemas?.[modelApi])
        .find((schema) => schema !== undefined)
    : undefined;
  const efforts = thinkingEfforts(settingsSchema);
  const thinkingOptions = [
    { value: "default", label: t("Default") },
    { value: "true", label: t("On (default effort)") },
    { value: "false", label: t("Off") },
    ...efforts.map((value) => ({
      value,
      label: t(value.charAt(0).toUpperCase() + value.slice(1)),
    })),
    ...(draft.thinking &&
    !["default", "true", "false", ...efforts].includes(draft.thinking)
      ? [{ value: draft.thinking, label: draft.thinking }]
      : []),
  ];
  const built = buildDraftConfig(draft, settingsSchema, t);
  const dirty = creating || draft.dirty;
  // Saving needs a name and a model. The save bar keeps an edit's save off
  // and says why; creation refuses the save and points at what is missing.
  const incomplete = unnamed || !draft.model;
  const nextVersion = (version ?? 0) + 1;
  const marked = sectionsOf(overridden ?? []);
  const mark = (section: EditorSection) =>
    marked.has(section) && (
      <Badge variant="info" size="sm">
        {t("Overridden")}
      </Badge>
    );

  function save(event: FormEvent<HTMLFormElement>) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    // Creation's button stays enabled: a refused save points at what is
    // missing instead of explaining a disabled button.
    if (incomplete) {
      setAttempted(true);
      if (unnamed) onUnnamed?.();
      else
        document
          .getElementById(modelId)
          ?.querySelector<HTMLElement>("button")
          ?.focus();
      return;
    }
    if (!built.ok) {
      if (built.stage === "model") {
        setModelValidation(built.error);
        setModelExpanded(true);
      } else if (built.stage === "toolSearch") {
        setAdvancedExpanded(true);
        setToolSearchValidation(built.error);
      } else if (built.stage === "reviewer") {
        setReviewPanel({ validation: built.error });
      } else {
        setModelValidation(undefined);
        setValidation(built.error);
        setAdvancedExpanded(true);
      }
      return;
    }
    if (!event.currentTarget.checkValidity()) {
      setAdvancedExpanded(true);
      setModelExpanded(true);
      event.currentTarget.reportValidity();
      return;
    }
    setModelValidation(undefined);
    setToolSearchValidation(undefined);
    setValidation(undefined);
    if (preset) {
      submit(
        {
          ...initial,
          model: built.config.model,
          model_settings: built.config.model_settings,
          instructions: built.config.instructions,
        },
        etag,
        note.trim() || null,
      );
    } else {
      submit(built.config, etag, note.trim() || null);
    }
  }

  return (
    <form id={id} onSubmit={save} noValidate>
      <DetailLayout
        rail={rail?.({
          modelName: selectedModel?.name ?? draft.model,
          modelIcon: selectedModel && (
            <ModelIcon
              upstream={selectedModel.config.model_name}
              catalogRef={selectedModel.catalog_ref}
              provider={providerBrand(selectedProvider)}
              size={16}
            />
          ),
          environmentId: draft.environmentTemplateId,
          skillCount: draft.skills.length,
          connectionCount: draft.connections.length,
          dirty,
        })}
      >
        <fieldset disabled={pending} className="fieldset-reset min-w-0">
          <Sections>
            {profile}
            <InstructionsSection
              draft={draft}
              readOnly={readonly}
              mark={mark("instructions")}
              officialInstructions={
                preset ? (preset.builtin_instructions ?? "") : undefined
              }
            />
            <ModelSection
              id={modelId}
              draft={draft}
              choices={choices}
              readOnly={readonly}
              mark={mark("model")}
              error={attempted ? t("Choose a model.") : undefined}
              settings={
                <ModelSettings
                  draft={draft}
                  thinkingOptions={thinkingOptions}
                  readOnly={readonly}
                  limited={!!preset}
                  expanded={modelExpanded}
                  onExpandedChange={setModelExpanded}
                  validation={modelValidation}
                />
              }
            />
            <SectionGroup
              title={t("Capabilities")}
              description={t(
                "The tools, skills and services the agent can use, and the models that read its media.",
              )}
            >
              <AgentToolsets
                value={draft.toolsets}
                onChange={draft.setToolsets}
                config={built.ok ? built.config : undefined}
                agentId={agentId}
                readOnly={configurationReadonly}
                mark={mark("toolsets")}
                onReview={(target) => setReviewPanel({ target })}
                onRequireReviewer={() => {
                  if (!draft.reviewer?.model) setReviewPanel({});
                }}
              />
              <SkillsSection
                draft={draft}
                choices={choices}
                readOnly={configurationReadonly}
                mark={mark("skills")}
              />
              <ConnectionsSection
                draft={draft}
                choices={choices}
                readOnly={configurationReadonly}
                mark={mark("connections")}
                onReview={(target) => setReviewPanel({ target })}
                onRequireReviewer={() => {
                  if (!draft.reviewer?.model) setReviewPanel({});
                }}
              />
              <MediaUnderstandingSection
                draft={draft}
                readOnly={configurationReadonly}
              />
            </SectionGroup>
            <AgentEnvironment
              value={draft.environmentTemplateId}
              onChange={draft.setEnvironmentTemplateId}
              disabled={pending}
              readOnly={configurationReadonly}
            />
            <ConfigurationGroup
              title={t("Advanced")}
              description={t("Memory, tool review and execution options.")}
              open={advancedExpanded}
              onOpenChange={setAdvancedExpanded}
            >
              <Section
                title={t("Memory")}
                description={t("Knowledge available to new sessions.")}
              >
                <MemorySection draft={draft} readOnly={configurationReadonly} />
              </Section>
              <Section
                title={t("Auto review")}
                description={t("Checks tools set to Auto review.")}
                className={styles.parameterSection}
              >
                <ReviewerSection
                  draft={draft}
                  choices={choices}
                  readOnly={configurationReadonly}
                  mark={mark("reviewer")}
                  onOpen={() => setReviewPanel({})}
                />
              </Section>
              <Section
                title={t("Tool discovery")}
                description={t("Choose how the agent finds tools.")}
                className={styles.parameterSection}
              >
                <ToolSearchSection
                  draft={draft}
                  choices={choices}
                  readOnly={configurationReadonly}
                  mark={mark("toolSearch")}
                  error={toolSearchValidation}
                />
              </Section>
              <Section
                title={t("Execution")}
                description={t("Control tool runs and incoming messages.")}
                className={styles.parameterSection}
              >
                <ExecutionFields
                  compact
                  value={draft.execution}
                  onChange={draft.setExecution}
                  readOnly={configurationReadonly}
                />
              </Section>
              <Section
                title={t("JSON")}
                description={t("Edit options not shown above.")}
              >
                <AdvancedSection
                  draft={draft}
                  readOnly={configurationReadonly}
                  mark={mark("advanced")}
                  validation={validation}
                />
              </Section>
            </ConfigurationGroup>
          </Sections>
        </fieldset>
      </DetailLayout>
      {dirty && !readonly && !creating && (
        <SaveBar
          title={t("Unsaved changes")}
          consequence={
            (!draft.model && t("Choose a model to save.")) ||
            t(
              "New work uses v{{version}} by default. Active work and approval continuations keep their original version.",
              {
                version: nextVersion,
              },
            )
          }
          note={note}
          onNoteChange={setNote}
          noteLabel={t("Version note")}
          notePlaceholder={t("Version note (optional)")}
          onDiscard={discard}
          saveLabel={saveLabel ?? t("Save changes")}
          pending={pending}
          disabled={!draft.model}
        />
      )}
      {reviewPanel && (
        <ReviewerSettings
          initial={draft.reviewer}
          target={reviewPanel.target}
          validation={reviewPanel.validation}
          models={choices.data?.models ?? []}
          settingsSchemas={Object.assign(
            {},
            ...(definitions.data?.items.map(
              (item) => item.settings_schemas ?? {},
            ) ?? []),
          )}
          readOnly={configurationReadonly}
          onApply={draft.setReviewer}
          onClose={() => setReviewPanel(undefined)}
        />
      )}
      <ErrorToast error={error ?? choices.error} />
    </form>
  );
}
