import {
  Button,
  Checkbox,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
} from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontalIcon } from "@phosphor-icons/react";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/form";
import { DeviceDirectory } from "../environments/device-directory";
import { jsonObject, runOverride } from "../../shared/validation";
import styles from "./conversations.module.css";

type Options = Omit<Schema["ThreadRunSubmissionIntent-Input"], "input">;
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
  const initialBinding =
    initial.environment && "environment_id" in initial.environment
      ? initial.environment
      : undefined;
  const [workingDirectory, setWorkingDirectory] = useState(
    initialBinding?.working_directory ?? "",
  );
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
    setEnvironment: (value: string) => {
      setEnvironment(value);
      setWorkingDirectory("");
    },
    workingDirectory,
    setWorkingDirectory,
    advanced,
    setAdvanced,
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
export function RunOptions({
  options,
  showAgent = true,
}: {
  options: ReturnType<typeof useRunOptions>;
  showAgent?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [open, setOpen] = useState(false);
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
  const selectedEnvironment = choices.data?.environments.find(
    (item) => options.environment === `instance:${item.id}`,
  );
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button type="button" size="sm" variant="ghost">
          {<SlidersHorizontalIcon size={14} />}
          {t("Options")}
        </Button>
      }
      size={"md"}
      title={t("Run options")}
      description={t(
        "Leave options inherited to keep the source configuration. Changes apply to the next run only.",
      )}
      closeLabel={t("Close")}
      open={open}
      footer={
        <Button type="button" onClick={() => setOpen(false)}>
          {t("Done")}
        </Button>
      }
    >
      <div className={styles.composerOptions}>
        <ErrorNotice error={choices.error} />
        {showAgent && (
          <ChoiceField
            placeholder={t("Inherit")}
            value={options.agent || "inherit"}
            onValueChange={(value) => {
              options.setAgent(value === "inherit" ? "" : value);
              options.setRevision("");
            }}
            label={t("Agent")}
            options={[
              { value: "inherit", label: t("Inherit") },
              ...(choices.data?.agents ?? [])
                .filter((agent) => agent.enabled)
                .map((agent) => ({
                  value: agent.id,
                  label: agent.name,
                })),
            ]}
          />
        )}
        <ChoiceField
          placeholder={t("Inherit")}
          value={options.model || "inherit"}
          onValueChange={(value) =>
            options.setModel(value === "inherit" ? "" : value)
          }
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
          placeholder={t("Inherit")}
          value={options.environment}
          onValueChange={(value) => options.setEnvironment(value)}
          label={t("Environment")}
          options={[
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
          ]}
        />
        <p className="text-sm text-muted-foreground">
          {t(
            "Create a new environment from a template, or reuse an existing target and its retained files.",
          )}
        </p>
        {selectedEnvironment?.device_id && open && (
          <DeviceDirectory
            key={selectedEnvironment.id}
            environmentId={selectedEnvironment.id}
            value={options.workingDirectory}
            onChange={options.setWorkingDirectory}
          />
        )}
        <Label className="flex items-center gap-2">
          <Checkbox
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
        <TextAreaField
          label={t("Model settings (JSON)")}
          value={options.settings}
          onChange={options.setSettings}
          rows={3}
          code
        />
        <DisclosureSection title={<>{t("Advanced configuration")}</>}>
          <div className={styles.composerOptions}>
            <FormField
              className="min-w-0 w-full"
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
