import { Badge, DisclosureSection } from "a13n-ui";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../../service-client";
import { ExecutionFields } from "../../../shared/forms";
import { ErrorToast } from "../../../shared/feedback";
import { DetailLayout, SaveBar } from "../../../shared/page";
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
import { ConnectionsSection, SkillsSection } from "./capabilities";
import { buildDraftConfig, thinkingEfforts, useAgentDraft } from "./draft";
import { InstructionsSection } from "./instructions";
import { MemorySection } from "./memory";
import { ReviewerSection, ReviewerSettings } from "./reviewer";
import type { ReviewTarget } from "../reviewer";
import { ModelSection } from "./model";
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
  agentId,
  initial,
  version,
  etag,
  pending,
  error,
  readonly = false,
  submit,
  discard,
  rail,
  identity,
  saveLabel,
  unnamed = false,
  overridden,
}: {
  /** The agent a save publishes a version of; none while creating. */
  agentId?: string;
  initial: AgentConfig;
  /** Omitted while creating: there is no published version yet. */
  version?: number;
  etag?: string;
  pending: boolean;
  error: unknown;
  readonly?: boolean;
  submit: (config: AgentConfig, etag?: string, note?: string | null) => void;
  discard?: () => void;
  rail?: (summary: AgentDraftSummary) => ReactNode;
  /** Name and description fields shown while creating. */
  identity?: ReactNode;
  saveLabel?: string;
  /** Creation waits for the name the identity fields hold. */
  unnamed?: boolean;
  /** Fields a read-only view's configuration changes from its version. */
  overridden?: Iterable<string>;
}) {
  const { t } = useTranslation();
  const creating = version === undefined;
  const draft = useAgentDraft(initial);
  const [note, setNote] = useState(""),
    [modelExpanded, setModelExpanded] = useState(false),
    [advancedExpanded, setAdvancedExpanded] = useState(false),
    [validation, setValidation] = useState<Error>(),
    [modelValidation, setModelValidation] = useState<Error>(),
    [toolSearchValidation, setToolSearchValidation] = useState<Error>();
  const [reviewPanel, setReviewPanel] = useState<{
    target?: ReviewTarget;
    validation?: Error;
  }>();
  const choices = useAgentChoices();
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
  const invalidResponse =
    error instanceof ApiError && [400, 422].includes(error.status);
  const dirty = creating || draft.dirty;
  // Saving stays off until these are filled, and the save bar says which.
  const blocker =
    unnamed && !draft.model
      ? t("Name the agent and choose a model to create it.")
      : unnamed
        ? t("Name the agent to create it.")
        : !draft.model
          ? t("Choose a model to save.")
          : undefined;
  const nextVersion = (version ?? 0) + 1;
  const marked = sectionsOf(overridden ?? []);
  const mark = (section: EditorSection) =>
    marked.has(section) && (
      <Badge variant="info" size="sm">
        {t("Overridden")}
      </Badge>
    );

  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    if (!built.ok) {
      if (built.stage === "model") {
        setModelValidation(built.error);
        setModelExpanded(true);
      } else if (built.stage === "toolSearch") {
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
    setModelValidation(undefined);
    setToolSearchValidation(undefined);
    setValidation(undefined);
    submit(built.config, etag, note.trim() || null);
  }

  return (
    <form onSubmit={save}>
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
        <fieldset disabled={pending} className="fieldset-reset grid gap-9">
          {identity}
          <ModelSection
            draft={draft}
            choices={choices}
            thinkingOptions={thinkingOptions}
            readOnly={readonly}
            mark={mark("model")}
            expanded={modelExpanded || (invalidResponse && !!modelValidation)}
            onExpandedChange={setModelExpanded}
            validation={modelValidation}
          />
          <InstructionsSection
            draft={draft}
            readOnly={readonly}
            mark={mark("instructions")}
          />
          <SkillsSection
            draft={draft}
            choices={choices}
            readOnly={readonly}
            mark={mark("skills")}
          />
          <ConnectionsSection
            draft={draft}
            choices={choices}
            readOnly={readonly}
            mark={mark("connections")}
            onReview={(target) => setReviewPanel({ target })}
            onRequireReviewer={() => {
              if (!draft.reviewer?.model) setReviewPanel({});
            }}
          />
          <ToolSearchSection
            draft={draft}
            choices={choices}
            readOnly={readonly}
            mark={mark("toolSearch")}
            error={toolSearchValidation}
          />
          <MemorySection draft={draft} readOnly={readonly} />
          <AgentToolsets
            value={draft.toolsets}
            onChange={draft.setToolsets}
            config={built.ok ? built.config : undefined}
            agentId={agentId}
            readOnly={readonly}
            mark={mark("toolsets")}
            onReview={(target) => setReviewPanel({ target })}
            onRequireReviewer={() => {
              if (!draft.reviewer?.model) setReviewPanel({});
            }}
          />
          <ReviewerSection
            draft={draft}
            choices={choices}
            readOnly={readonly}
            mark={mark("reviewer")}
            onOpen={() => setReviewPanel({})}
          />
          <AgentEnvironment
            value={draft.environmentTemplateId}
            onChange={draft.setEnvironmentTemplateId}
            disabled={pending}
            readOnly={readonly}
          />
          <DisclosureSection title={t("Execution policy")}>
            <p className="mb-4 text-sm text-muted-foreground">
              {t(
                "Defaults for new runs. Each field can be overridden by a thread or a single run.",
              )}
            </p>
            <ExecutionFields
              value={draft.execution}
              onChange={draft.setExecution}
              readOnly={readonly}
            />
          </DisclosureSection>
          <AdvancedSection
            draft={draft}
            readOnly={readonly}
            mark={mark("advanced")}
            expanded={advancedExpanded || (invalidResponse && !!validation)}
            onExpandedChange={setAdvancedExpanded}
            validation={validation}
          />
        </fieldset>
      </DetailLayout>
      {dirty && !readonly && (
        <SaveBar
          title={creating ? t("New agent") : t("Unsaved changes")}
          consequence={
            blocker ??
            (creating
              ? t("Saving creates the agent, ready for a conversation.")
              : t(
                  "New work uses v{{version}} by default. Active work and approval continuations keep their original version.",
                  {
                    version: nextVersion,
                  },
                ))
          }
          note={creating ? undefined : note}
          onNoteChange={creating ? undefined : setNote}
          noteLabel={t("Version note")}
          notePlaceholder={t("Version note (optional)")}
          onDiscard={discard}
          discardLabel={creating ? t("Cancel") : undefined}
          saveLabel={saveLabel ?? t("Save changes")}
          pending={pending}
          disabled={!!blocker}
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
          readOnly={readonly}
          onApply={draft.setReviewer}
          onClose={() => setReviewPanel(undefined)}
        />
      )}
      <ErrorToast error={error ?? choices.error} />
    </form>
  );
}
