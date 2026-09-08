import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Checkbox, Input, Select } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { TextArea } from "../../shared/form";
import { jsonObject, runOverride } from "../../shared/validation";
import { useIdempotency } from "../../shared/idempotency";
import { Composer } from "./composer";
import styles from "./conversations.module.css";

type Options = Omit<Schema["ThreadRunSubmissionIntent-Input"], "input">;
export function OptionsComposer({
  initial,
  submit,
  label,
  disabled,
  commandBasis,
}: {
  initial?: Schema["ThreadRunSubmissionIntent-Input"];
  submit: (
    intent: Schema["ThreadRunSubmissionIntent-Input"],
    key: string,
  ) => Promise<unknown>;
  label?: string;
  disabled?: boolean;
  commandBasis?: unknown;
}) {
  const options = useRunOptions(initial),
    idempotency = useIdempotency();
  return (
    <Composer
      initial={initial?.input}
      disabled={disabled}
      label={label}
      submit={async (input) => {
        const intent = { ...options.build(), input };
        await submit(intent, idempotency.forBody([commandBasis, intent]));
        idempotency.reset();
      }}
    >
      <RunOptions options={options} />
    </Composer>
  );
}
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
  const choices = useQuery({
    queryKey: ["run-options", workspace.id],
    queryFn: async ({ signal }) => {
      const path = { workspace_id: workspace.id };
      const [agents, models, templates, environments] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/agents", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/models", {
              params: { path, query: { cursor, enabled: true } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
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
  return (
    <details>
      <summary>{t("Run options")}</summary>
      <div className={styles.composerOptions}>
        <p className={styles.notice}>
          {t(
            "Leave options inherited to keep the source configuration. Changes apply to the next run only.",
          )}
        </p>
        <ErrorNotice error={choices.error} />
        {showAgent && (
          <Select
            label={t("Agent")}
            placeholder={t("Inherit")}
            value={options.agent || "inherit"}
            onValueChange={(value) => {
              options.setAgent(value === "inherit" ? "" : value);
              options.setRevision("");
            }}
            options={[
              { value: "inherit", label: t("Inherit") },
              ...(choices.data?.agents ?? [])
                .filter((agent) => agent.enabled)
                .map((agent) => ({ value: agent.id, label: agent.name })),
            ]}
          />
        )}
        <Select
          label={t("Model")}
          placeholder={t("Inherit")}
          value={options.model || "inherit"}
          onValueChange={(value) =>
            options.setModel(value === "inherit" ? "" : value)
          }
          options={[
            { value: "inherit", label: t("Inherit") },
            ...(choices.data?.models ?? []).map((model) => ({
              value: model.key,
              label: model.name,
            })),
          ]}
        />
        <Select
          label={t("Environment")}
          placeholder={t("Inherit")}
          value={options.environment}
          onValueChange={options.setEnvironment}
          options={[
            { value: "inherit", label: t("Inherit") },
            { value: "none", label: t("No environment") },
            ...(choices.data?.templates ?? []).map((template) => ({
              value: `template:${template.id}`,
              label: `${t("Template")}: ${template.name}`,
            })),
            ...(choices.data?.environments ?? []).map((environment) => ({
              value: `instance:${environment.id}`,
              label: environment.id,
            })),
          ]}
        />
        <Checkbox
          label={t("Override instructions")}
          checked={options.overrideInstructions}
          onCheckedChange={(value) =>
            options.setOverrideInstructions(value === true)
          }
        />
        {options.overrideInstructions && (
          <TextArea
            label={t("Instructions override")}
            value={options.instructions}
            onChange={options.setInstructions}
            rows={4}
          />
        )}
        <TextArea
          label={t("Model settings (JSON)")}
          value={options.settings}
          onChange={options.setSettings}
          rows={3}
          code
        />
        <details>
          <summary>{t("Advanced configuration")}</summary>
          <div className={styles.composerOptions}>
            <Input
              label={t("Pinned agent revision ID")}
              value={options.revision}
              onChange={(event) => options.setRevision(event.target.value)}
            />
            <TextArea
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
        </details>
      </div>
    </details>
  );
}
