import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "@converge.ai/a13n";
import { Input, Picker, Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { jsonObject } from "../../shared/validation";
import { ErrorNotice } from "../../shared/feedback";
import { TextArea } from "../../shared/form";
import { advancedConfig, buildConfig, type AgentConfig } from "./configuration";
import styles from "./agents.module.css";
import { useAgentChoices } from "./choices";
import { AgentCapabilities } from "./capabilities";
import { Check, Circle } from "lucide-react";

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
  context,
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
  context?: ReactNode;
}) {
  const [initial] = useState(providedInitial),
    [originalVersion] = useState(version);
  const { t } = useTranslation();
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
  const choices = useAgentChoices();
  useEffect(() => {
    if (error instanceof ApiError && [400, 422].includes(error.status))
      setExpanded(true);
  }, [error]);
  function save(event: FormEvent) {
    if (event.target !== event.currentTarget) return;
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
  const dirty =
    creating ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model.model_key ||
    settings !== JSON.stringify(initial.model.settings ?? {}, null, 2) ||
    advanced !== advancedConfig(initial) ||
    JSON.stringify(skills) !== JSON.stringify(initial.skills ?? []) ||
    JSON.stringify(mcp) !== JSON.stringify(initial.mcp_tools ?? []) ||
    JSON.stringify(connectors) !==
      JSON.stringify(initial.connector_tools ?? []);
  return (
    <form onSubmit={save} className={styles.editor}>
      <div className={styles.saveBar}>
        <div className={styles.savePanel}>
          <span className={styles.saveStatus} role="status">
            {dirty ? <Circle size={12} /> : <Check size={14} />}{" "}
            {t(dirty ? "Unsaved changes" : "All changes saved")}
          </span>
          {version !== undefined && (
            <p>
              {t("Current version")} <strong>v{version}</strong>
            </p>
          )}
          {!readonly && (
            <Button
              type="submit"
              variant="primary"
              loading={pending}
              disabled={
                pending || !dirty || !model || (creating && !name.trim())
              }
            >
              {t(creating ? "Create agent" : "Save changes")}
            </Button>
          )}
          {reload && dirty && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={pending}
              onClick={reload}
            >
              {t("Discard changes")}
            </Button>
          )}
        </div>
        <div className={styles.historyActions}>{context}</div>
      </div>
      <fieldset disabled={pending || readonly} className="fieldset-reset">
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
          </section>
          <section className={styles.section}>
            <h2>{t("Instructions")}</h2>
            <p>
              {t(
                "Define the agent's role, approach, and the outcomes you expect.",
              )}
            </p>
            <TextArea
              label={t("System instructions")}
              hideLabel
              value={instructions}
              onChange={setInstructions}
              rows={14}
            />
          </section>
          <AgentCapabilities
            choices={choices}
            skills={skills}
            setSkills={setSkills}
            mcp={mcp}
            setMcp={setMcp}
            connectors={connectors}
            setConnectors={setConnectors}
          />
          <details
            className={styles.advanced}
            open={expanded}
            onToggle={(event) => setExpanded(event.currentTarget.open)}
          >
            <summary>{t("Advanced configuration")}</summary>
            <div>
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
      </fieldset>

      <ErrorNotice error={error ?? choices.error} retry={reload} />
    </form>
  );
}
