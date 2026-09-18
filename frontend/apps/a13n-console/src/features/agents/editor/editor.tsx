import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../../service-client";
import { ErrorToast } from "../../../shared/feedback";
import { DetailLayout, SaveBar, Section } from "../../../shared/page";
import { useMemoryProviders } from "../../memory/availability";
import { AgentMemorySelection } from "../../memory/selection";
import { ModelIcon } from "../../models/model-icon";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import { useAgentChoices } from "../choices";
import type { AgentConfig } from "../configuration";
import { AgentEnvironment } from "../environment";
import { AgentToolsets } from "../toolsets";
import { AdvancedSection } from "./advanced";
import { ConnectionsSection, SkillsSection } from "./capabilities";
import { buildDraftConfig, useAgentDraft } from "./draft";
import { InstructionsSection } from "./instructions";
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
  /** Name, description, and avatar fields shown while creating. */
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
  const { visible: memoryVisible } = useMemoryProviders();
  const selectedModel = choices.data?.models.find(
    (item) => item.key === draft.model,
  );
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
    ...(draft.thinking &&
    !["true", "false", ...thinkingEfforts].includes(draft.thinking)
      ? [{ value: draft.thinking, label: draft.thinking }]
      : []),
  ];
  const invalidResponse =
    error instanceof ApiError && [400, 422].includes(error.status);
  const dirty = creating || draft.dirty;
  const nextVersion = (version ?? 0) + 1;

  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    const result = buildDraftConfig(draft, settingsSchema, t);
    if (!result.ok) {
      if (result.stage === "model") {
        setModelValidation(result.error);
        setModelExpanded(true);
      } else {
        setModelValidation(undefined);
        setValidation(result.error);
        setAdvancedExpanded(true);
      }
      return;
    }
    setModelValidation(undefined);
    setValidation(undefined);
    submit(result.config, etag, note.trim() || null);
  }

  return (
    <form onSubmit={save}>
      <DetailLayout
        rail={rail?.({
          modelName: selectedModel?.name ?? draft.model,
          modelIcon: selectedModel && (
            <ModelIcon
              upstream={selectedModel.upstream_model}
              catalogRef={selectedModel.catalog_ref}
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
          <AgentToolsets
            value={draft.toolsets}
            onChange={draft.setToolsets}
            reviewer={initial.reviewer}
            readOnly={readonly}
          />
          <AgentEnvironment
            value={draft.environmentTemplateId}
            onChange={draft.setEnvironmentTemplateId}
            disabled={readonly || pending}
          />
          {(memoryVisible || draft.memory || initial.memory) && (
            <Section
              title={t("Memory")}
              description={t(
                "Remember useful information across runs, with explicit control over what is stored.",
              )}
            >
              <AgentMemorySelection
                agentId={agentId}
                savedProviderId={initial.memory?.provider_id}
                readOnly={readonly}
                value={draft.memory}
                onChange={draft.setMemory}
              />
            </Section>
          )}
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
              ? t("Saving creates the agent and publishes v1.")
              : t("Saving publishes v{{version}} and makes it the default.", {
                  version: nextVersion,
                })
          }
          note={creating ? undefined : note}
          onNoteChange={creating ? undefined : setNote}
          noteLabel={t("Version note")}
          notePlaceholder={t("Version note (optional)")}
          onDiscard={discard}
          saveLabel={
            saveLabel ?? t("Save as v{{version}}", { version: nextVersion })
          }
          pending={pending}
          disabled={saveDisabled || !draft.model}
        />
      )}
      <ErrorToast error={error ?? choices.error} />
    </form>
  );
}
