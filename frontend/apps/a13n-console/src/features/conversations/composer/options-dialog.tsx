import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
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
import styles from "./composer.module.css";

export type OptionField = "agent" | "model" | "environment" | "instructions";

type Options = Omit<Schema["ThreadRunSubmissionIntent-Input"], "input">;

/** The next run's overrides, held beside the message they will be sent with. */
export function useRunOptions(initial: Options = {}) {
  const [original] = useState(initial),
    [agent, setAgent] = useState(initial.agent_id ?? ""),
    [revision, setRevision] = useState(initial.agent_revision_id ?? ""),
    [model, setModel] = useState(
      initial.config_override?.model?.model_key ?? "",
    );
  const [settings, setSettings] = useState(
      initial.config_override?.model?.settings
        ? JSON.stringify(initial.config_override.model.settings, null, 2)
        : "",
    ),
    [instructions, setInstructions] = useState(
      initial.config_override?.instructions ?? "",
    );
  const [overrideInstructions, setOverrideInstructions] = useState(
    initial.config_override?.instructions !== undefined,
  );
  const [environment, setEnvironment] = useState(
    initial.environment === null
      ? "none"
      : initial.environment && "template_id" in initial.environment
        ? `template:${initial.environment.template_id}`
        : initial.environment
          ? `instance:${initial.environment.environment_id}`
          : "inherit",
  );
  /** Chips name what was chosen, so the picked labels travel with the values. */
  const [labels, setLabels] = useState<{
    model?: string;
    environment?: string;
    agent?: string;
  }>({});
  const [advanced, setAdvanced] = useState(
    JSON.stringify(
      Object.fromEntries(
        Object.entries(initial.config_override ?? {}).filter(
          ([key]) => !["model", "instructions", "plugins"].includes(key),
        ),
      ),
      null,
      2,
    ),
  );
  return {
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
    setEnvironment,
    advanced,
    setAdvanced,
    labels,
    setLabels,
    build: (): Options => {
      const extra = jsonObject(advanced);
      for (const key of ["model", "instructions", "plugins"])
        if (key in extra)
          throw new Error(
            `The ${key} field cannot be edited in advanced run configuration.`,
          );
      const override = runOverride({
        ...extra,
        ...(original.config_override?.plugins
          ? { plugins: original.config_override.plugins }
          : {}),
        ...(model ||
        settings.trim() ||
        original.config_override?.model?.characteristics
          ? {
              model: {
                ...(original.config_override?.model?.characteristics
                  ? {
                      characteristics:
                        original.config_override.model.characteristics,
                    }
                  : {}),
                ...(model ? { model_key: model } : {}),
                ...(settings.trim() ? { settings: jsonObject(settings) } : {}),
              },
            }
          : {}),
        ...(overrideInstructions ? { instructions } : {}),
      });
      return {
        ...original,
        agent_id: agent || undefined,
        agent_revision_id: revision || undefined,
        config_override: Object.keys(override).length ? override : undefined,
        ...(environment === "inherit"
          ? { environment: undefined }
          : {
              environment:
                environment === "none"
                  ? null
                  : environment.startsWith("template:")
                    ? { template_id: environment.slice(9) }
                    : { environment_id: environment.slice(9) },
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
    { workspace } = useWorkspace();
  const choices = useQuery({
    queryKey: ["run-options", workspace.id],
    enabled: open,
    queryFn: async ({ signal }) => {
      const path = { workspace: workspace.id };
      const [agents, models, templates, environments] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/agents", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/models", {
              params: { path, query: { cursor, enabled: true } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/environment-templates", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/environments", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
        ),
      ]);
      return { agents, models, templates, environments };
    },
  });
  useEffect(() => {
    if (!open || !focus) return;
    const timer = setTimeout(
      () => document.getElementById(`run-option-${focus}`)?.focus(),
      0,
    );
    return () => clearTimeout(timer);
  }, [open, focus]);
  const environmentOptions = [
    { value: "inherit", label: t("Inherit") },
    { value: "none", label: t("No environment") },
    ...(choices.data?.templates ?? []).map((template) => ({
      value: `template:${template.id}`,
      label: `${t("Create from template")}: ${template.name}`,
    })),
    ...(choices.data?.environments ?? []).map((environment) => ({
      value: `instance:${environment.id}`,
      label: `${t("Reuse existing")}: ${environment.name} (${environment.id})`,
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
              ...(choices.data?.agents ?? [])
                .filter((agent) => agent.enabled)
                .map((agent) => ({ value: agent.id, label: agent.name })),
            ]}
          />
        )}
        <ChoiceField
          id="run-option-model"
          placeholder={t("Inherit")}
          value={options.model || "inherit"}
          onValueChange={(value) => {
            options.setModel(value === "inherit" ? "" : value);
            options.setLabels((previous) => ({
              ...previous,
              model: choices.data?.models.find((item) => item.key === value)
                ?.name,
            }));
          }}
          label={t("Model")}
          options={[
            { value: "inherit", label: t("Inherit") },
            ...(choices.data?.models ?? []).map((model) => ({
              value: model.key,
              label: model.name,
            })),
          ]}
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
            "Create from template allocates a new environment. Reuse existing keeps the same environment and its retained files, including across sessions. A stopped managed target resumes; a deleted managed target is recreated without old files.",
          )}
          options={environmentOptions}
        />
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
