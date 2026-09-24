import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
  SearchPicker,
  SettingsSection,
  Switch,
} from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { allPages, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { jsonObject, runOverride, TextAreaField } from "../../../shared/forms";
import { environmentTemplates } from "../../environments/api";
import { WorkingDirectory } from "../../environments/working-directory";
import { modelApi } from "../../models/api";
import {
  InheritIcon,
  MediaUnderstandingFields,
  mediaKinds,
  mediaSelected,
  modelOption,
  modelPopupWidth,
  useMediaSummary,
  useMediaUnderstandingChoices,
  useWorkspaceMediaDefault,
  type MediaKind,
  type ModelIdentity,
} from "../../models/media-understanding-fields";
import { MemoryOptions } from "./memory-options";
import styles from "./composer.module.css";

export type OptionField =
  "agent" | "model" | "environment" | "memories" | "instructions" | MediaKind;

/** Media rows own their identifiers in the shared selector. */
export function optionFieldId(field: OptionField) {
  return mediaKinds.some((entry) => entry.kind === field)
    ? `media-understanding-${field}`
    : `run-option-${field}`;
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
  const [settings, setSettings] = useState(""),
    [instructions, setInstructions] = useState("");
  const [overrideInstructions, setOverrideInstructions] = useState(false);
  const [environment, setEnvironment] = useState("inherit"),
    [workingDirectory, setWorkingDirectory] = useState("");
  const [memories, setMemories] = useState<Schema["MemoryMount"][]>([]);
  /** Chips name what was chosen, so the picked labels travel with the values. */
  const [labels, setLabels] = useState<{
    model?: string;
    environment?: string;
    agent?: string;
    media?: Partial<Record<MediaKind, string>>;
  }>({});
  const [advanced, setAdvanced] = useState("{}");
  return {
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
    settings,
    setSettings,
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
        ...(model || settings.trim()
          ? {
              model: {
                ...(model ? { model_id: model } : {}),
                ...(settings.trim() ? { settings: jsonObject(settings) } : {}),
              },
            }
          : {}),
        ...(mediaSelected(mediaUnderstanding).length
          ? { media_understanding: mediaUnderstanding }
          : {}),
        ...(overrideInstructions ? { instructions } : {}),
      });
      return {
        agent_id: agent || undefined,
        agent_revision_id: revision || undefined,
        ...(Object.keys(override).length
          ? { options: { overrides: override } }
          : {}),
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

export function RunOptionsDialog({
  options,
  showAgent = true,
  open,
  onOpenChange,
  focus,
}: {
  options: RunOptionsState;
  showAgent?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  focus?: OptionField;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, organization } = useWorkspace();
  const choices = useQuery({
    queryKey: ["run-options", workspace.id],
    enabled: open,
    queryFn: async ({ signal }) => {
      const api = modelApi(client, organization.id, {
        kind: "workspace",
        id: workspace.id,
      });
      const path = { workspace_id: workspace.id };
      const [agents, models, templates, environments] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/agents", {
              params: { path, query: { cursor, archived: false } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          api.models(signal, cursor, undefined, undefined, true),
        ),
        allPages((cursor) =>
          environmentTemplates(client, workspace.id, signal, cursor),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/environments", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
        ),
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
  const environmentOptions = [
    { value: "inherit", label: t("Inherit") },
    ...(choices.data?.templates ?? []).map((template) => ({
      value: `template:${template.id}`,
      label: `${t("Create from template")}: ${template.name}`,
    })),
    // A deleted environment is never mounted again.
    ...(choices.data?.environments ?? [])
      .filter((item) => item.status !== "deleting" && item.status !== "deleted")
      .map((item) => ({
        value: `instance:${item.id}`,
        label: `${t("Reuse existing")}: ${item.name} (${item.id})`,
      })),
  ];
  return (
    <ModalFrame
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("Run options")}
      description={t(
        "Leave options inherited to keep the source configuration. Changes apply to the next run only.",
      )}
      closeLabel={t("Close")}
      footer={
        <Button type="button" onClick={() => onOpenChange(false)}>
          {t("Apply")}
        </Button>
      }
    >
      <div className={styles.optionFields}>
        <ErrorNotice error={choices.error} />
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
                agent: choices.data?.agents.find((item) => item.id === value)
                  ?.name,
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
        <ModelOptions
          options={options}
          models={choices.data?.models}
          focus={focus}
        />
        <ChoiceField
          id="run-option-environment"
          placeholder={t("Inherit")}
          value={options.environment}
          onValueChange={(value) => {
            options.setEnvironment(value);
            options.setLabels((previous) => ({
              ...previous,
              environment: environmentOptions.find(
                (option) => option.value === value,
              )?.label,
            }));
          }}
          label={t("Environment")}
          description={t(
            "Create from template allocates a new environment. Reuse existing keeps the same environment and its retained files, including across sessions. A stopped environment starts again when the run needs it.",
          )}
          options={environmentOptions}
        />
        {selectedEnvironment?.device_id && open && (
          <WorkingDirectory
            key={selectedEnvironment.id}
            value={options.workingDirectory}
            onChange={options.setWorkingDirectory}
          />
        )}
        {open && <MemoryOptions options={options} focus={focus} />}
        <Label className={styles.optionSwitch}>
          <Switch
            id="run-option-instructions"
            checked={options.overrideInstructions}
            onCheckedChange={(value) =>
              options.setOverrideInstructions(value === true)
            }
          />
          {t("Override instructions")}
        </Label>
        {options.overrideInstructions && (
          <TextAreaField
            label={t("Instructions override")}
            value={options.instructions}
            onChange={options.setInstructions}
            rows={4}
          />
        )}
        <DisclosureSection title={<>{t("Advanced")}</>}>
          <div className={styles.optionFields}>
            <TextAreaField
              label={t("Model settings (JSON)")}
              value={options.settings}
              onChange={options.setSettings}
              rows={3}
              code
            />
            <FormField
              className="w-full min-w-0"
              label={t("Pinned agent revision ID")}
            >
              <Input
                value={options.revision}
                onChange={(event) => options.setRevision(event.target.value)}
              />
            </FormField>
            <TextAreaField
              label={t("Run configuration (JSON)")}
              hint={t(
                "Skills, MCP and connector tools, client tools, output format, retries, and subagents.",
              )}
              value={options.advanced}
              onChange={options.setAdvanced}
              code
              rows={8}
            />
          </div>
        </DisclosureSection>
      </div>
    </ModalFrame>
  );
}

/**
 * The model this run reasons with and the models that read media for it, in
 * one block. Mounted with the dialog, so the model lookups run only once it
 * opens and the chosen names travel to the chips.
 */
function ModelOptions({
  options,
  models,
  focus,
}: {
  options: RunOptionsState;
  models?: ModelIdentity[];
  focus?: OptionField;
}) {
  const { t } = useTranslation();
  const identity = useMediaUnderstandingChoices();
  const workspaceDefault = useWorkspaceMediaDefault();
  const summary = useMediaSummary();
  // A chip that names a kind opens the disclosure it lives in, so the focused picker is in view.
  const focusedKind = mediaKinds.some((entry) => entry.kind === focus);
  const [mediaExpanded, setMediaExpanded] = useState(focusedKind);
  useEffect(() => {
    if (focusedKind) setMediaExpanded(true);
  }, [focusedKind]);
  return (
    <>
      <FormField label={t("Model")}>
        <SearchPicker
          id="run-option-model"
          label={t("Model")}
          placeholder={t("Inherit")}
          emptyMessage={t(
            "No models available. Configure a provider and model first.",
          )}
          popupClassName={modelPopupWidth}
          value={options.model || "inherit"}
          groups={[
            {
              label: t("Model"),
              options: [
                {
                  value: "inherit",
                  label: t("Inherit"),
                  icon: <InheritIcon />,
                },
                ...(models ?? []).map((model) =>
                  modelOption(model, identity.providerName(model.provider_id)),
                ),
              ],
            },
          ]}
          onValueChange={(value) => {
            options.setModel(value === "inherit" ? "" : value);
            options.setLabels((previous) => ({
              ...previous,
              model: models?.find((item) => item.id === value)?.name,
            }));
          }}
        />
      </FormField>
      <DisclosureSection
        title={t("Media understanding")}
        summary={summary(options.mediaUnderstanding, t("Inherit"))}
        open={mediaExpanded}
        onOpenChange={setMediaExpanded}
      >
        <SettingsSection variant="plain">
          <MediaUnderstandingFields
            value={options.mediaUnderstanding}
            explainEmpty={false}
            inherit={{
              label: t("Inherit"),
              describe: (kind) => {
                const model = workspaceDefault(kind);
                return model
                  ? t("Agent or workspace default · {{model}}", { model })
                  : t("Agent or workspace default");
              },
            }}
            onChange={(value, kind) => {
              options.setMediaUnderstanding(value);
              const key = value[kind];
              options.setLabels((previous) => ({
                ...previous,
                media: {
                  ...previous.media,
                  [kind]: key ? identity.find(key)?.name : undefined,
                },
              }));
            }}
          />
        </SettingsSection>
      </DisclosureSection>
    </>
  );
}
