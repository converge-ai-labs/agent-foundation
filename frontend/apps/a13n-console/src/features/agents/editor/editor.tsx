import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../../service-client";
import { ErrorToast } from "../../../shared/feedback";
import { DetailLayout, SaveBar } from "../../../shared/page";
import { ModelIcon } from "../../models/model-icon";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import { useAgentChoices } from "../choices";
import type { AgentConfig } from "../configuration";
import { AgentEnvironment } from "../environment";
import { AgentToolsets } from "../toolsets";
import { AdvancedSection } from "./advanced";
import { ConnectionsSection, SkillsSection } from "./capabilities";
import { buildDraftConfig, thinkingEfforts, useAgentDraft } from "./draft";
import { InstructionsSection } from "./instructions";
import { MemorySection } from "./memory";
import { ModelSection } from "./model";

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
  saveDisabled = false,
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
  saveDisabled?: boolean;
}) {
  const { t } = useTranslation();
  const creating = version === undefined;
  const draft = useAgentDraft(initial);
  const [note, setNote] = useState(""),
    [modelExpanded, setModelExpanded] = useState(false),
    [advancedExpanded, setAdvancedExpanded] = useState(false),
    [validation, setValidation] = useState<Error>(),
    [modelValidation, setModelValidation] = useState<Error>();
  const choices = useAgentChoices();
  const definitions = useModelProviderDefinitions();
  const selectedModel = choices.data?.models.find(
    (item) => item.key === draft.model,
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
  const nextVersion = (version ?? 0) + 1;

  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    if (!built.ok) {
      if (built.stage === "model") {
        setModelValidation(built.error);
        setModelExpanded(true);
      } else {
        setModelValidation(undefined);
        setValidation(built.error);
        setAdvancedExpanded(true);
      }
      return;
    }
    setModelValidation(undefined);
    setValidation(undefined);
    submit(built.config, etag, note.trim() || null);
  }

  return (
    <form onSubmit={save}>
      <DetailLayout
        rail={rail?.({
          modelName: selectedModel?.name ?? draft.model,
          modelIcon: selectedModel && (
            <ModelIcon upstream={selectedModel.config.model_name} size={16} />
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
            expanded={modelExpanded || (invalidResponse && !!modelValidation)}
            onExpandedChange={setModelExpanded}
            validation={modelValidation}
          />
          <InstructionsSection draft={draft} readOnly={readonly} />
          <SkillsSection draft={draft} choices={choices} readOnly={readonly} />
          <ConnectionsSection
            draft={draft}
            choices={choices}
            readOnly={readonly}
          />
          <MemorySection draft={draft} readOnly={readonly} />
          <AgentToolsets
            value={draft.toolsets}
            onChange={draft.setToolsets}
            config={built.ok ? built.config : undefined}
            agentId={agentId}
            readOnly={readonly}
          />
          <AgentEnvironment
            value={draft.environmentTemplateId}
            onChange={draft.setEnvironmentTemplateId}
            disabled={readonly || pending}
          />
          <AdvancedSection
            draft={draft}
            readOnly={readonly}
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
            creating
              ? t("Saving creates the agent, ready for a conversation.")
              : t(
                  "New work uses v{{version}} by default. Active work and approval continuations keep their original version.",
                  {
                    version: nextVersion,
                  },
                )
          }
          note={creating ? undefined : note}
          onNoteChange={creating ? undefined : setNote}
          noteLabel={t("Version note")}
          notePlaceholder={t("Version note (optional)")}
          onDiscard={discard}
          discardLabel={creating ? t("Cancel") : undefined}
          saveLabel={saveLabel ?? t("Save changes")}
          pending={pending}
          disabled={saveDisabled || !draft.model}
        />
      )}
      <ErrorToast error={error ?? choices.error} />
    </form>
  );
}
