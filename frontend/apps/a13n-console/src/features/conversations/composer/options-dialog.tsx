import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
  SearchPicker,
  Switch,
} from "a13n-ui";
import { MonitorIcon, StackIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { allPages, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import {
  FormSection,
  ExecutionFields,
  executionOverride,
  jsonObject,
  runOverride,
  TextAreaField,
} from "../../../shared/forms";
import { environmentTemplates } from "../../environments/api";
import { WorkingDirectory } from "../../environments/working-directory";
import { modelApi } from "../../models/api";
import {
  InheritIcon,
  mediaSelected,
} from "../../models/media-understanding-fields";
import { ModelOptions, MediaOptions } from "./model-options";
import { MemoryOptions } from "./memory-options";
import styles from "./composer.module.css";

export type OptionField = "agent" | "environment" | "memories";

export function optionFieldId(field: OptionField) {
  return `run-option-${field}`;
}

/**
 * The environment a new Thread mounts as its primary one: a new environment
 * reserved from a template, or an existing one. Without a choice the agent's
 * own template is reserved when its first Run is accepted.
 */
export type EnvironmentChoice =
  Schema["ManagedEnvironmentCreate"] | Omit<Schema["MountCreate"], "name">;

/** What a message chooses for the Run it starts, beside its payload. */
type Options = Pick<
  Schema["NewThread"],
  "agent_revision_id" | "options" | "memories"
> & {
  agent_id?: string;
  environment?: EnvironmentChoice;
};

/** Overrides with their own controls; advanced JSON must not restate them. */
const dedicatedOverrides = ["model", "instructions", "media_understanding"];

/** The next run's overrides, held beside the message they will be sent with. */
export function useRunOptions() {
  const [agent, setAgent] = useState(""),
    [revision, setRevision] = useState(""),
    [model, setModel] = useState("");
  const [mediaUnderstanding, setMediaUnderstanding] = useState<
    Schema["MediaUnderstandingSelection"]
  >({});
  const [instructions, setInstructions] = useState("");
  const [overrideInstructions, setOverrideInstructions] = useState(false);
  const [environment, setEnvironment] = useState("inherit"),
    [workingDirectory, setWorkingDirectory] = useState("");
  const [memories, setMemories] = useState<Schema["MemoryMount"][]>([]);
  /** Chips name what was chosen, so the picked labels travel with the values. */
  const [labels, setLabels] = useState<{
    environment?: string;
    agent?: string;
  }>({});
  const [advanced, setAdvanced] = useState("{}");
  const [execution, setExecution] = useState<Schema["ExecutionOverride"]>({});
  const values = {
    execution,
    agent,
    revision,
    model,
    mediaUnderstanding,
    instructions,
    overrideInstructions,
    environment,
    workingDirectory,
    memories,
    labels,
    advanced,
  };
  let modified = true;
  try {
    modified = !!(
      agent ||
      revision ||
      model ||
      overrideInstructions ||
      memories.length ||
      environment !== "inherit" ||
      mediaSelected(mediaUnderstanding).length ||
      executionOverride(execution) ||
      Object.keys(jsonObject(advanced)).length
    );
  } catch {
    /* Invalid draft still counts as a local change. */
  }
  return {
    modified,
    reset: () => {
      setAgent("");
      setRevision("");
      setModel("");
      setInstructions("");
      setOverrideInstructions(false);
      setMediaUnderstanding({});
      setExecution({});
      setEnvironment("inherit");
      setWorkingDirectory("");
      setMemories([]);
      setLabels({});
      setAdvanced("{}");
    },
    /** Every choice at once, so a dialog dismissed without applying can put them back. */
    values,
    restore: (saved: typeof values) => {
      setAgent(saved.agent);
      setRevision(saved.revision);
      setModel(saved.model);
      setMediaUnderstanding(saved.mediaUnderstanding);
      setInstructions(saved.instructions);
      setOverrideInstructions(saved.overrideInstructions);
      setEnvironment(saved.environment);
      setWorkingDirectory(saved.workingDirectory);
      setMemories(saved.memories);
      setLabels(saved.labels);
      setAdvanced(saved.advanced);
      setExecution(saved.execution);
    },
    execution,
    setExecution,
    mediaUnderstanding,
    setMediaUnderstanding,
    overrideInstructions,
    setOverrideInstructions,
    agent,
    setAgent,
    revision,
    setRevision,
    model,
    setModel,
    instructions,
    setInstructions,
    environment,
    setEnvironment: (value: string) => {
      setEnvironment(value);
      setWorkingDirectory("");
    },
    workingDirectory,
    setWorkingDirectory,
    memories,
    setMemories,
    advanced,
    setAdvanced,
    labels,
    setLabels,
    build: (): Options => {
      const extra = jsonObject(advanced);
      for (const key of dedicatedOverrides)
        if (key in extra)
          throw new Error(
            `The ${key} field cannot be edited in advanced run configuration.`,
          );
      const override = runOverride({
        ...extra,
        ...(model ? { model } : {}),
        ...(mediaSelected(mediaUnderstanding).length
          ? { media_understanding: mediaUnderstanding }
          : {}),
        ...(overrideInstructions ? { instructions } : {}),
      });
      const policy = executionOverride(execution);
      const runOptions = {
        ...(Object.keys(override).length ? { overrides: override } : {}),
        ...(policy ? { execution: policy } : {}),
      };
      return {
        agent_id: agent || undefined,
        agent_revision_id: revision || undefined,
        ...(Object.keys(runOptions).length ? { options: runOptions } : {}),
        ...(memories.length ? { memories } : {}),
        ...(environment === "inherit"
          ? {}
          : {
              environment: environment.startsWith("template:")
                ? { template_id: environment.slice(9) }
                : {
                    environment_id: environment.slice(9),
                    ...(workingDirectory
                      ? { working_directory: workingDirectory }
                      : {}),
                  },
            }),
      };
    },
  };
}

export type RunOptionsState = ReturnType<typeof useRunOptions>;
export type RunOptionValues = RunOptionsState["values"];

export function RunOptionsDialog({
  options,
  showAgent = true,
  newThread = true,
  agentId,
  open,
  onOpenChange,
  onCancel,
  focus,
}: {
  options: RunOptionsState;
  showAgent?: boolean;
  newThread?: boolean;
  agentId?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Closes the dialog and puts back the choices it opened with. */
  onCancel: () => void;
  focus?: OptionField;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [validation, setValidation] = useState<unknown>();
  useEffect(() => {
    if (open) setValidation(undefined);
  }, [open]);
  const choices = useQuery({
    queryKey: ["run-options", workspace.id, newThread],
    enabled: open,
    queryFn: async ({ signal }) => {
      const api = modelApi(client, workspace.id);
      const [agents, models, templates, environments] = await Promise.all([
        allPages((cursor) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/agents", {
              params: { query: { cursor, archived: false } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          api.models(signal, cursor, undefined, undefined, true),
        ),
        newThread
          ? allPages((cursor) =>
              environmentTemplates(client, workspace.id, signal, cursor),
            )
          : [],
        newThread
          ? allPages((cursor) =>
              client
                .workspace(workspace.id)
                .GET("/api/v1/environments", {
                  params: { query: { cursor } },
                  signal,
                })
                .then(data),
            )
          : [],
      ]);
      return { agents, models, templates, environments };
    },
  });
  const selectedEnvironment = choices.data?.environments.find(
    (item) => options.environment === `instance:${item.id}`,
  );
  useEffect(() => {
    if (!open || !focus) return;
    const timer = setTimeout(
      () => document.getElementById(optionFieldId(focus))?.focus(),
      0,
    );
    return () => clearTimeout(timer);
  }, [open, focus]);
  // Each choice is its name, with what choosing it does on a quieter line.
  const environmentOptions = [
    { value: "inherit", label: t("Inherit"), icon: <InheritIcon /> },
    ...(choices.data?.templates ?? []).map((template) => ({
      value: `template:${template.id}`,
      label: template.name,
      description: t("Create from template"),
      icon: <StackIcon size={20} className={styles.optionGlyph} />,
    })),
    // A deleted environment is never mounted again.
    ...(choices.data?.environments ?? [])
      .filter((item) => item.status !== "deleting" && item.status !== "deleted")
      .map((item) => ({
        value: `instance:${item.id}`,
        label: item.name,
        description: `${t("Reuse existing")} · ${item.id}`,
        keywords: [item.id],
        icon: <MonitorIcon size={20} className={styles.optionGlyph} />,
      })),
  ];
  return (
    <ModalFrame
      open={open}
      // Dismissing is cancelling; only Apply keeps the changes.
      onOpenChange={(next) => (next ? onOpenChange(true) : onCancel())}
      size="md"
      title={t("Run options")}
      description={t(
        "Changes apply to the next run only. Unchanged settings are inherited.",
      )}
      closeLabel={t("Close")}
      footer={
        <div className={styles.settingsFooter}>
          <Button
            type="button"
            variant="ghost"
            disabled={!options.modified}
            onClick={() => {
              options.reset();
              setValidation(undefined);
            }}
          >
            {t("Restore defaults")}
          </Button>
          <div className={styles.settingsActions}>
            <Button type="button" variant="outline" onClick={onCancel}>
              {t("Cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => {
                try {
                  options.build();
                  setValidation(undefined);
                  onOpenChange(false);
                } catch (error) {
                  setValidation(error);
                }
              }}
            >
              {t("Apply")}
            </Button>
          </div>
        </div>
      }
    >
      <div className={styles.settingsPanel}>
        <ErrorNotice error={choices.error} />
        <FormSection title={t("Model and instructions")}>
          <ModelOptions
            options={options}
            models={choices.data?.models}
            agentId={options.agent || agentId}
            revisionId={
              options.revision ||
              choices.data?.agents.find(
                (agent) => agent.id === (options.agent || agentId),
              )?.default_revision_id ||
              undefined
            }
          />
          <div className={styles.instructionsRow}>
            <div className={styles.instructionsLabel}>
              <Label htmlFor="run-option-instructions">
                {t("Override instructions")}
              </Label>
              <p
                id="run-option-instructions-hint"
                className={styles.settingsHint}
              >
                {t("Replace the agent's instructions for this run.")}
              </p>
            </div>
            <Switch
              id="run-option-instructions"
              aria-describedby="run-option-instructions-hint"
              checked={options.overrideInstructions}
              onCheckedChange={(value) =>
                options.setOverrideInstructions(value === true)
              }
            />
          </div>
          {options.overrideInstructions && (
            <TextAreaField
              label={t("Instructions override")}
              value={options.instructions}
              onChange={options.setInstructions}
              rows={4}
            />
          )}
        </FormSection>
        <div className={styles.settingsDisclosures}>
          <DisclosureSection
            className={styles.settingsGroup}
            title={t("Execution policy")}
          >
            <ExecutionFields
              value={options.execution}
              onChange={options.setExecution}
            />
          </DisclosureSection>
          <DisclosureSection
            className={styles.settingsGroup}
            title={t("Advanced overrides")}
          >
            <MediaOptions options={options} />
            <TextAreaField
              label={t("Overrides (JSON)")}
              hint={t(
                "Model parameters, tools, skills and other agent overrides. Model, instructions and media use the controls above.",
              )}
              value={options.advanced}
              onChange={options.setAdvanced}
              code
              rows={7}
            />
            <FormField label={t("Pinned agent revision ID")}>
              <Input
                value={options.revision}
                onChange={(event) => options.setRevision(event.target.value)}
              />
            </FormField>
          </DisclosureSection>
        </div>
        {newThread && (
          <DisclosureSection
            className={styles.settingsGroup}
            title={t("Conversation setup")}
            defaultOpen={
              !!focus && ["agent", "environment", "memories"].includes(focus)
            }
          >
            <p className={styles.settingsHint}>
              {t("Agent, environment and memories for the new conversation.")}
            </p>
            {showAgent && (
              <ChoiceField
                id="run-option-agent"
                placeholder={t("Inherit")}
                value={options.agent || "inherit"}
                onValueChange={(value) => {
                  options.setAgent(value === "inherit" ? "" : value);
                  options.setRevision("");
                  options.setLabels((previous) => ({
                    ...previous,
                    agent: choices.data?.agents.find(
                      (item) => item.id === value,
                    )?.name,
                  }));
                }}
                label={t("Agent")}
                options={[
                  { value: "inherit", label: t("Inherit") },
                  ...(choices.data?.agents ?? []).map((agent) => ({
                    value: agent.id,
                    label: agent.name,
                  })),
                ]}
              />
            )}
            <FormField
              label={t("Environment")}
              description={t(
                "Create from template allocates a new environment. Reuse existing keeps the same environment and its retained files, including across sessions. A stopped environment starts again when the run needs it.",
              )}
            >
              <SearchPicker
                id="run-option-environment"
                label={t("Environment")}
                placeholder={t("Search environments")}
                emptyMessage={t("No matching environments")}
                value={options.environment}
                groups={[
                  { label: t("Environment"), options: environmentOptions },
                ]}
                onValueChange={(value) => {
                  options.setEnvironment(value);
                  options.setLabels((previous) => ({
                    ...previous,
                    environment: environmentOptions.find(
                      (option) => option.value === value,
                    )?.label,
                  }));
                }}
              />
            </FormField>
            {selectedEnvironment && open && (
              <WorkingDirectory
                key={selectedEnvironment.id}
                value={options.workingDirectory}
                onChange={options.setWorkingDirectory}
              />
            )}
            {open && <MemoryOptions options={options} focus={focus} />}
          </DisclosureSection>
        )}
        <ErrorNotice error={validation} />
      </div>
    </ModalFrame>
  );
}
