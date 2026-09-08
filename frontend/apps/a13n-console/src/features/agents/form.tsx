import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@converge.ai/a13n";
import { useQuery } from "@tanstack/react-query";
import { Checkbox, Input, Picker, Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { jsonObject } from "../../shared/validation";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextArea } from "../../shared/form";
import { advancedConfig, buildConfig, type AgentConfig } from "./configuration";
import styles from "./agents.module.css";
import shared from "../../shared/shared.module.css";

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
}) {
  const [initial] = useState(providedInitial),
    [originalVersion] = useState(version);
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [name, setName] = useState(initialName),
    [description, setDescription] = useState(initialDescription),
    [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model.model_key),
    [settings, setSettings] = useState(
      JSON.stringify(initial.model.settings ?? {}, null, 2),
    ),
    [advanced, setAdvanced] = useState(advancedConfig(initial)),
    [expanded, setExpanded] = useState(false),
    [validation, setValidation] = useState<Error>();
  const [skills, setSkills] = useState(initial.skills ?? []),
    [mcp, setMcp] = useState(initial.mcp_tools ?? []),
    [connectors, setConnectors] = useState(initial.connector_tools ?? []);
  const choices = useQuery({
    queryKey: ["agent-choices", workspace.id],
    queryFn: async ({ signal }) => {
      const path = { workspace_id: workspace.id };
      const [models, skills, mcp, connectors] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/models", {
              params: { path, query: { cursor, limit: 100, enabled: true } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/skills", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/mcp-connections", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/connector-connections", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
      ]);
      return { models, skills, mcp, connectors };
    },
  });
  useEffect(() => {
    if (error instanceof ApiError && [400, 422].includes(error.status))
      setExpanded(true);
  }, [error]);
  function save(event: FormEvent) {
    event.preventDefault();
    try {
      const config = buildConfig(
        initial,
        {
          instructions,
          model: {
            ...initial.model,
            model_key: model,
            settings: jsonObject(settings),
          },
          skills,
          mcp_tools: mcp,
          connector_tools: connectors,
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
    }
  }
  return (
    <form onSubmit={save}>
      <fieldset disabled={pending || readonly} className="fieldset-reset">
        <div className={styles.editor}>
          <div className={styles.main}>
            {creating && (
              <section className={styles.section}>
                <Input
                  label={t("Agent name")}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                  maxLength={128}
                />
                <Input
                  label={t("Description")}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  maxLength={4096}
                />
              </section>
            )}
            <section className={styles.section}>
              <h2>{t("Instructions")}</h2>
              <p>
                {t(
                  "Define the agent's role, approach, and the outcomes you expect.",
                )}
              </p>
              <TextArea
                label={t("System instructions")}
                value={instructions}
                onChange={setInstructions}
                rows={14}
              />
            </section>
            <section className={styles.section}>
              <h2>{t("Model")}</h2>
              <Picker
                label={t("Model")}
                placeholder={t("Choose a model…")}
                emptyMessage={t(
                  "No models available. Configure a provider and model first.",
                )}
                value={model}
                onValueChange={setModel}
                groups={[
                  {
                    label: t("Available models"),
                    options:
                      choices.data?.models.map((item) => ({
                        value: item.key,
                        label: item.name,
                        description: `${item.key} · ${item.upstream_model}`,
                      })) ?? [],
                  },
                ]}
              />
              <TextArea
                label={t("Model settings")}
                hint={t(
                  "Settings override the selected model's defaults. Use a JSON object.",
                )}
                code
                value={settings}
                onChange={setSettings}
                rows={4}
              />
            </section>
            <details
              className={styles.advanced}
              open={expanded}
              onToggle={(event) => setExpanded(event.currentTarget.open)}
            >
              <summary>{t("Advanced configuration")}</summary>
              <div>
                <TextArea
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
            </details>
          </div>
          <aside className={styles.aside}>
            <section className={styles.section}>
              <h2>{t("Skills")}</h2>
              <p>{t("Reusable knowledge and procedures.")}</p>
              <div className={styles.selections}>
                {choices.data?.skills.map((skill) => {
                  const selected = skills.find(
                    (item) => item.skill_key === skill.key,
                  );
                  return (
                    <div key={skill.id}>
                      <Checkbox
                        label={skill.name}
                        checked={!!selected}
                        onCheckedChange={(checked) =>
                          setSkills((previous) =>
                            checked
                              ? [...previous, { skill_key: skill.key }]
                              : previous.filter(
                                  (item) => item.skill_key !== skill.key,
                                ),
                          )
                        }
                      />
                      {selected && (
                        <Input
                          label={t("Pinned version")}
                          type="number"
                          min={1}
                          value={selected.version ?? ""}
                          placeholder={t("Latest")}
                          onChange={(event) =>
                            setSkills((previous) =>
                              previous.map((item) =>
                                item.skill_key === skill.key
                                  ? {
                                      ...item,
                                      version: event.target.value
                                        ? Number(event.target.value)
                                        : null,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                      )}
                    </div>
                  );
                })}
                {!choices.isPending && !choices.data?.skills.length && (
                  <small>{t("No skills available")}</small>
                )}
              </div>
            </section>
            <section className={styles.section}>
              <h2>{t("MCP connections")}</h2>
              <div className={styles.selections}>
                {choices.data?.mcp.map((connection) => {
                  const selected = mcp.find(
                    (item) => item.mcp_connection_id === connection.id,
                  );
                  return (
                    <div key={connection.id}>
                      <Checkbox
                        label={connection.name}
                        checked={!!selected}
                        onCheckedChange={(checked) =>
                          setMcp((previous) =>
                            checked
                              ? [
                                  ...previous,
                                  { mcp_connection_id: connection.id },
                                ]
                              : previous.filter(
                                  (item) =>
                                    item.mcp_connection_id !== connection.id,
                                ),
                          )
                        }
                      />
                      {selected && (
                        <Input
                          label={t("Tool names")}
                          hint={t("Comma-separated; empty selects all.")}
                          value={selected.tools?.join(", ") ?? ""}
                          onChange={(event) =>
                            setMcp((previous) =>
                              previous.map((item) =>
                                item.mcp_connection_id === connection.id
                                  ? {
                                      ...item,
                                      tools: event.target.value.trim()
                                        ? event.target.value
                                            .split(",")
                                            .map((name) => name.trim())
                                            .filter(Boolean)
                                        : null,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                      )}
                    </div>
                  );
                })}
                {!choices.isPending && !choices.data?.mcp.length && (
                  <small>{t("No MCP connections available")}</small>
                )}
              </div>
            </section>
            <section className={styles.section}>
              <h2>{t("Connectors")}</h2>
              <div className={styles.selections}>
                {choices.data?.connectors.map((connection) => (
                  <Checkbox
                    key={connection.id}
                    label={connection.name}
                    checked={connectors.some(
                      (item) => item.connector_connection_id === connection.id,
                    )}
                    onCheckedChange={(checked) =>
                      setConnectors((previous) =>
                        checked
                          ? [
                              ...previous,
                              { connector_connection_id: connection.id },
                            ]
                          : previous.filter(
                              (item) =>
                                item.connector_connection_id !== connection.id,
                            ),
                      )
                    }
                  />
                ))}
                {!choices.isPending && !choices.data?.connectors.length && (
                  <small>{t("No connectors available")}</small>
                )}
              </div>
            </section>
          </aside>
        </div>
      </fieldset>
      <ErrorNotice error={error ?? choices.error} retry={reload} />
      {!readonly && (
        <div className={shared.formActions}>
          <FormActions
            pending={pending}
            label={t(creating ? "Create agent" : "Save new version")}
          />
          {reload && (
            <Button variant="ghost" disabled={pending} onClick={reload}>
              {t("Reload latest")}
            </Button>
          )}
        </div>
      )}
    </form>
  );
}
