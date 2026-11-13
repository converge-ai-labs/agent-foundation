import { useId, useState } from "react";
import { Link } from "react-router";
import { FormField, Input, Switch, Textarea } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useSelectors } from "../transport/context";
import { ModelIcon } from "../shell/model-identity";
import {
  SettingsChoice,
  SettingsRow,
  SettingsSection,
} from "../shell/settings";
import { ConfirmAction } from "../shell/confirm-action";
import { ResourceChoice } from "./resource-choice";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function BotFields({
  source,
  onChange,
  setup = false,
}: {
  source: string;
  onChange: (source: string, key: string, value: unknown) => void;
  setup?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const selectors = useSelectors();
  const document = readDocument(source);
  const get = (key: string) => document?.getIn(["webui", "bot", key]);
  const set = (key: string, value: unknown) =>
    onChange(updateDocument(source, ["webui", "bot", key], value), key, value);
  const enabled = get("enabled") === true;
  const heartbeat = get("heartbeat_enabled") !== false;
  const interval = Number(get("interval_seconds") ?? 3600);
  const presets = [900, 1800, 3600, 7200, 14400, 28800, 86400];
  const [custom, setCustom] = useState(!presets.includes(interval));
  const models = selectors.data?.models ?? [];
  const agents = selectors.data?.agents ?? [];
  const defaultAgent = agents.find(
    (agent) => agent.agent_id === document?.getIn(["defaults", "agent"]),
  );
  const selectedAgent = get("agent")
    ? agents.find((agent) => agent.agent_id === get("agent"))
    : defaultAgent;
  const defaultModel = models.find(
    (model) => model.model_id === selectedAgent?.model_id,
  );
  return (
    <SettingsSection title={setup ? undefined : t("Bot")}>
      {!setup && (
        <SettingsRow
          label={t("Bot")}
          controlId={`${id}-enabled`}
          description={t(
            "Keeps track of work across chats. First enable sends a visible workspace introduction using the selected model.",
          )}
        >
          {enabled ? (
            <ConfirmAction
              trigger={
                <Switch
                  id={`${id}-enabled`}
                  checked
                  nativeButton
                  render={<button type="button" />}
                />
              }
              title={t("Turn off Bot?")}
              description={t(
                "History, settings, notes and pending inputs are kept. Active work finishes and other chats keep running. Retained inputs can be processed after you turn Bot back on.",
              )}
              confirmLabel={t("Turn off")}
              onConfirm={() => set("enabled", false)}
            />
          ) : (
            <Switch
              id={`${id}-enabled`}
              checked={false}
              onCheckedChange={(value) => set("enabled", value)}
            />
          )}
        </SettingsRow>
      )}
      {(setup || enabled) && (
        <>
          <ResourceChoice
            label={t("Bot agent")}
            description={t(
              "Execution settings only; Bot keeps its own instructions.",
            )}
            value={String(get("agent") ?? "")}
            options={[
              {
                value: "",
                label: defaultAgent
                  ? t("Default ({{name}})", { name: defaultAgent.name })
                  : t("Default (built-in settings)"),
              },
              ...agents.map((agent) => ({
                value: agent.agent_id,
                label: agent.name,
              })),
            ]}
            onValueChange={(value) => set("agent", value || null)}
            loading={selectors.isLoading}
          />
          <ResourceChoice
            label={t("Bot model")}
            description={t(
              "Default follows the selected Agent's model. Changes apply to later runs.",
            )}
            value={String(get("model") ?? "")}
            options={[
              {
                value: "",
                label: defaultModel
                  ? t("Default ({{name}})", {
                      name: defaultModel.name,
                    })
                  : t("Default (no model)"),
                icon: defaultModel ? (
                  <ModelIcon model={defaultModel} />
                ) : undefined,
              },
              ...models.map((model) => ({
                value: model.model_id,
                label: model.name,
                icon: <ModelIcon model={model} />,
              })),
            ]}
            onValueChange={(value) => set("model", value || null)}
            loading={selectors.isLoading}
          />
          <ErrorNotice
            error={selectors.error}
            retry={() => void selectors.refetch()}
          />
          {!get("model") && !defaultModel && (
            <p className={styles.fieldGroup}>
              {models.length ? (
                t("Choose an existing model to continue.")
              ) : (
                <Link to="/settings/models">{t("Add a model first.")}</Link>
              )}
            </p>
          )}
          <p className={styles.fieldGroup}>
            {t(
              "Access follows the selected Agent, or built-in defaults without an Agent. Environment permissions still apply.",
            )}
          </p>
          {[
            { key: "files_enabled", label: t("Files access") },
            { key: "shell_enabled", label: t("Shell access") },
            { key: "computer_enabled", label: t("Computer access") },
          ].map(({ key, label }) => (
            <SettingsChoice
              key={key}
              label={label}
              value={get(key) == null ? "inherit" : String(get(key))}
              options={[
                { value: "inherit", label: t("Follow Agent") },
                { value: "true", label: t("Enabled") },
                { value: "false", label: t("Disabled") },
              ]}
              onValueChange={(value) =>
                set(key, value === "inherit" ? null : value === "true")
              }
            />
          ))}
          <div className={styles.fieldGroup}>
            <FormField
              label={t("Custom instructions")}
              description={t(
                "Added to Bot's built-in instructions and global AGENTS.md.",
              )}
            >
              <Textarea
                variant="soft"
                rows={3}
                placeholder={t("Optional")}
                value={String(get("instructions") ?? "")}
                onChange={(event) => set("instructions", event.target.value)}
              />
            </FormField>
          </div>
          <SettingsRow
            label={t("Heartbeat")}
            controlId={`${id}-heartbeat`}
            description={t(
              "The first check is scheduled one interval after enabling. Turning off Heartbeat still allows selected Connector events.",
            )}
          >
            <Switch
              id={`${id}-heartbeat`}
              checked={heartbeat}
              onCheckedChange={(value) => set("heartbeat_enabled", value)}
            />
          </SettingsRow>
          {heartbeat && (
            <>
              <SettingsChoice
                label={t("Check in every")}
                value={custom ? "custom" : String(interval)}
                options={[
                  ...presets.map((value) => ({
                    value: String(value),
                    label:
                      value < 3600
                        ? t("{{count}} minutes", { count: value / 60 })
                        : value === 3600
                          ? t("1 hour")
                          : t("{{count}} hours", { count: value / 3600 }),
                  })),
                  { value: "custom", label: t("Custom…") },
                ]}
                onValueChange={(value) => {
                  setCustom(value === "custom");
                  if (value !== "custom")
                    set("interval_seconds", Number(value));
                }}
              />
              {custom && (
                <SettingsRow
                  label={t("Interval (minutes)")}
                  controlId={`${id}-interval`}
                >
                  <Input
                    id={`${id}-interval`}
                    type="number"
                    min={1}
                    max={1440}
                    step="any"
                    value={interval / 60}
                    onChange={(event) => {
                      const minutes = Number(event.target.value);
                      if (
                        Number.isFinite(minutes) &&
                        minutes >= 1 &&
                        minutes <= 1440
                      )
                        set("interval_seconds", Math.round(minutes * 60));
                    }}
                  />
                </SettingsRow>
              )}
            </>
          )}
        </>
      )}
    </SettingsSection>
  );
}
