import { Button, DisclosureSection, FormField, Input } from "a13n-ui";

import { SearchPicker } from "a13n-ui";

import { ApiError } from "@converge.ai/a13n";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";

import {
  ArrowLeft,
  Check,
  Circle,
  Layers,
  Maximize2,
  Minimize2,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/form";
import { jsonObject } from "../../shared/validation";
import styles from "./agents.module.css";
import { AgentSearchSelection } from "../search/selection";
import { AgentCapabilities } from "./capabilities";
import { useAgentChoices } from "./choices";
import { advancedConfig, buildConfig, type AgentConfig } from "./configuration";

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
  primaryAction,
  metadata,
  back,
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
  primaryAction?: ReactNode;
  metadata?: ReactNode;
  back: string;
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
    [instructionsExpanded, setInstructionsExpanded] = useState(false),
    [validation, setValidation] = useState<Error>();
  const [search, setSearch] = useState(initial.search ?? null);
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
          search,
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
    JSON.stringify(search) !== JSON.stringify(initial.search ?? null) ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model.model_key ||
    settings !== JSON.stringify(initial.model.settings ?? {}, null, 2) ||
    advanced !== advancedConfig(initial) ||
    JSON.stringify(skills) !== JSON.stringify(initial.skills ?? []) ||
    JSON.stringify(mcp) !== JSON.stringify(initial.mcp_tools ?? []) ||
    JSON.stringify(connectors) !==
      JSON.stringify(initial.connector_tools ?? []);
  return (
    <div className={styles.editorPage}>
      <Link className={styles.back} to={back}>
        <ArrowLeft size={14} />
        {t("Agents")}
      </Link>
      <form onSubmit={save} className={styles.editor}>
        <header className={styles.identity}>
          <div className={styles.identityHeading}>
            <span className={styles.agentIcon}>
              <Sparkles size={18} strokeWidth={1.5} />
            </span>
            <h1>{creating ? t("Create agent") : initialName}</h1>
          </div>
          {(creating || initialDescription) && (
            <p>
              {creating
                ? t("Start with clear instructions and the right model.")
                : initialDescription}
            </p>
          )}
        </header>
        <fieldset disabled={pending || readonly} className="fieldset-reset">
          <div className={styles.main}>
            {creating && (
              <section className={styles.section}>
                <FormField className="min-w-0 w-full" label={t("Agent name")}>
                  <Input
                    required={true}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    maxLength={128}
                  />
                </FormField>
                <FormField className="min-w-0 w-full" label={t("Description")}>
                  <Input
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    maxLength={4096}
                  />
                </FormField>
              </section>
            )}
            <section className={styles.modelProperty}>
              <h2>{t("Model")}</h2>
              <SearchPicker
                label={t("Model")}
                placeholder={t("Choose a model…")}
                emptyMessage={t(
                  "No models available. Configure a provider and model first.",
                )}
                value={model}
                groups={[
                  {
                    label: t("Available models"),
                    options:
                      choices.data?.models.map((item) => ({
                        value: item.key,
                        label: item.name,
                        icon: <Layers size={14} />,
                        description: [
                          ...new Set([item.key, item.upstream_model]),
                        ]
                          .filter((value) => value !== item.name)
                          .join(" · "),
                      })) ?? [],
                  },
                ]}
                onValueChange={setModel}
              />
            </section>
            <section className={styles.instructions}>
              <header>
                <h2>{t("Instructions")}</h2>
                <div>
                  <span>Markdown</span>
                  <Button
                    variant="ghost"
                    aria-label={t(
                      instructionsExpanded
                        ? "Collapse instructions"
                        : "Expand instructions",
                    )}
                    onClick={() =>
                      setInstructionsExpanded(!instructionsExpanded)
                    }
                    size="icon-sm"
                    type="button"
                  >
                    {instructionsExpanded ? (
                      <Minimize2 size={14} />
                    ) : (
                      <Maximize2 size={14} />
                    )}
                  </Button>
                </div>
              </header>
              <TextAreaField
                label={t("System instructions")}
                hideLabel
                value={instructions}
                onChange={setInstructions}
                rows={instructionsExpanded ? 24 : 9}
              />
              <footer>
                <span>
                  {t("The role, boundaries, and approach for this agent.")}
                </span>
                <span>
                  {t("{{count}} characters", { count: instructions.length })}
                </span>
              </footer>
            </section>
            <AgentSearchSelection value={search} onChange={setSearch} />
            <AgentCapabilities
              choices={choices}
              skills={skills}
              setSkills={setSkills}
              mcp={mcp}
              setMcp={setMcp}
              connectors={connectors}
              setConnectors={setConnectors}
            />
            <DisclosureSection
              className={styles.advanced}
              open={expanded}
              onOpenChange={(isExpanded) =>
                ((event) => setExpanded(event.currentTarget.open))({
                  currentTarget: { open: isExpanded },
                })
              }
              title={<>{t("Advanced configuration")}</>}
            >
              <div>
                <TextAreaField
                  label={t("Model settings")}
                  hint={t(
                    "Settings override the selected model's defaults. Use a JSON object.",
                  )}
                  code
                  value={settings}
                  onChange={setSettings}
                  rows={4}
                />
                <TextAreaField
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
            </DisclosureSection>
          </div>
        </fieldset>

        <aside className={styles.saveBar} aria-label={t("Agent actions")}>
          {primaryAction}
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
                variant="default"
                disabled={
                  pending || !dirty || !model || (creating && !name.trim())
                }
                loading={pending}
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
          {metadata}
          <div className={styles.historyActions}>{context}</div>
        </aside>
        <div className={styles.editorError}>
          <ErrorNotice error={error ?? choices.error} retry={reload} />
        </div>
      </form>
    </div>
  );
}
