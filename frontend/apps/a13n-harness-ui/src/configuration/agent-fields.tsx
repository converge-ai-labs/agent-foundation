import { useTranslation } from "react-i18next";
import { ToolSearchFields } from "./tool-search";
import { ModelIdentity } from "../shell/model-identity";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import {
  SettingsRow,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import layout from "./settings.module.css";
import type { ReactNode } from "react";
import { ArrowCounterClockwise, Trash } from "@phosphor-icons/react";
import management from "./management.module.css";
import { Link } from "react-router";
import { useRootConfiguration } from "./root-configuration";
import { useQuery } from "@tanstack/react-query";
import { Button, FormField, Textarea } from "a13n-ui";
import { ResourceChoice } from "./resource-choice";
import { capabilityLabel } from "./capability-label";
import { AgentNativeTools, nativeToolKind } from "./native-tools";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function AgentFields({
  source,
  onChange,
  connections,
}: {
  connections?: ReactNode;
  source: string;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const sources = useSources();
  const selectors = useSelectors();
  const root = useRootConfiguration();
  const agentName = (id: string) =>
    selectors.data?.agents.find((item) => item.agent_id === id)?.name ?? id;
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => result(client.GET("/api/catalog", { signal })),
  });
  const document = readDocument(source);
  const raw = document?.toJS() ?? {};
  const capabilities: { capability: string }[] = Array.isArray(raw.capabilities)
    ? raw.capabilities
    : [];
  const children: { agent?: string; markdown?: string }[] = Array.isArray(
    raw.subagents,
  )
    ? raw.subagents
    : [];
  const remove = (axis: string, index: number) => {
    if (!document) return;
    document.deleteIn([axis, index]);
    onChange(document.toString());
  };
  const add = (axis: string, value: object) => {
    if (!document) return;
    if (!document.has(axis)) document.set(axis, []);
    document.addIn([axis], value);
    onChange(document.toString());
  };
  const accepted = sources.data?.sources.some(
    (item) =>
      item.resource_kind === "agent" && item.resource_ids.includes(raw.id),
  );
  const proxy = useQuery({
    queryKey: ["agent-tool-search", raw.id],
    enabled: !!accepted,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/agents/{agent_id}/tool-search", {
          params: { path: { agent_id: raw.id } },
          signal,
        }),
      ),
  });
  if (!document)
    return (
      <p>{t("Fix the YAML in Advanced configuration before editing tools.")}</p>
    );
  if (
    (raw.capabilities !== undefined &&
      (!Array.isArray(raw.capabilities) ||
        !capabilities.every(
          (item) => item && typeof item.capability === "string",
        ))) ||
    (raw.subagents !== undefined &&
      (!Array.isArray(raw.subagents) ||
        !children.every(
          (item) =>
            item &&
            (typeof item.agent === "string" ||
              typeof item.markdown === "string"),
        ))) ||
    (raw.tools != null &&
      (!Array.isArray(raw.tools) ||
        !raw.tools.every((item: unknown) => typeof item === "string")))
  )
    return (
      <p>
        {t(
          "Fix the tool and subagent lists in Advanced configuration to use these fields.",
        )}
      </p>
    );
  const workingIndex = capabilities.findIndex(
    (item) => item.capability === "working_state",
  );
  const working = raw.capabilities?.[workingIndex]?.configuration ?? {};
  const setWorking = (key: string, value: boolean) => {
    if (workingIndex < 0)
      add("capabilities", {
        capability: "working_state",
        configuration: { [key]: value },
      });
    else {
      document.setIn(
        ["capabilities", workingIndex, "configuration", key],
        value,
      );
      onChange(document.toString());
    }
  };
  return (
    <>
      <ErrorNotice error={catalog.error || proxy.error} />
      <SettingsSection title={t("Tools & capabilities")}>
        <SettingsToggle
          label={t("Tasks")}
          description={t("Track the work this agent plans and completes.")}
          checked={working.tasks_enabled ?? true}
          onCheckedChange={(checked) => setWorking("tasks_enabled", checked)}
        />
        <SettingsToggle
          label={t("Notes")}
          description={t("Keep working notes during a chat.")}
          checked={working.notes_enabled ?? true}
          onCheckedChange={(checked) => setWorking("notes_enabled", checked)}
        />
        <details className={layout.groupDisclosure}>
          <summary>
            <span>{t("Model tools")}</span>
            <small>{t("Tools provided by the selected model")}</small>
          </summary>
          <div>
            <AgentNativeTools source={source} onChange={onChange} />
          </div>
        </details>
        <details className={layout.groupDisclosure}>
          <summary>
            <span>{t("Capabilities")}</span>
            <small>
              {t("Add skills, web access and other installed capabilities")}
            </small>
          </summary>
          <div className={management.capabilities}>
            {capabilities.map(
              (item, index) =>
                !nativeToolKind(item) &&
                item.capability !== "working_state" && (
                  <div className={management.capability} key={index}>
                    <div className={management.capabilityHeader}>
                      <span className={management.inlineLabel}>
                        {capabilityLabel(item.capability, t)}
                      </span>
                      <Button
                        variant="ghost"
                        aria-label={
                          [
                            "file_context",
                            "user_interaction",
                            "codeact",
                          ].includes(item.capability)
                            ? t("Reset capability {{name}}", {
                                name: capabilityLabel(item.capability, t),
                              })
                            : t("Remove capability {{name}}", {
                                name: capabilityLabel(item.capability, t),
                              })
                        }
                        onClick={() => remove("capabilities", index)}
                      >
                        {[
                          "file_context",
                          "user_interaction",
                          "codeact",
                        ].includes(item.capability) ? (
                          <ArrowCounterClockwise aria-hidden="true" />
                        ) : (
                          <Trash aria-hidden="true" />
                        )}
                        {[
                          "file_context",
                          "user_interaction",
                          "codeact",
                        ].includes(item.capability)
                          ? t("Reset to default")
                          : t("Remove")}
                      </Button>
                    </div>
                    <CapabilityOptions
                      source={source}
                      index={index}
                      onChange={onChange}
                    />
                  </div>
                ),
            )}
            <ResourceChoice
              loading={catalog.isPending}
              label={t("Add capability")}
              value=""
              onValueChange={(capability) => {
                if (capability)
                  add("capabilities", { capability, configuration: {} });
              }}
              options={[
                { value: "", label: t("Choose capability") },
                ...(catalog.data ?? [])
                  .filter(
                    (item) =>
                      item.kind === "capability" &&
                      item.configurable &&
                      ![
                        "NativeTool",
                        "native_image_generation",
                        "working_state",
                      ].includes(item.key) &&
                      !capabilities.some(
                        (selected) => selected.capability === item.key,
                      ),
                  )
                  .map((item) => ({
                    value: item.key,
                    label: capabilityLabel(item.key, t),
                  })),
              ]}
            />
          </div>
        </details>
      </SettingsSection>
      {connections && (
        <SettingsSection title={t("Connections")}>
          {connections}
        </SettingsSection>
      )}
      <ToolSearchFields source={source} onChange={onChange} />
      <SettingsSection variant="grouped" title={t("Delegation")}>
        {children.map((item, index) => (
          <SettingsRow
            key={index}
            label={item.agent ? agentName(item.agent) : item.markdown!}
            description={
              item.agent ? (
                <ModelIdentity
                  model={selectors.data?.models?.find(
                    (model) =>
                      model.model_id ===
                      selectors.data?.agents.find(
                        (agent) => agent.agent_id === item.agent,
                      )?.model_id,
                  )}
                  name={
                    selectors.data?.models?.find(
                      (model) =>
                        model.model_id ===
                        selectors.data?.agents.find(
                          (agent) => agent.agent_id === item.agent,
                        )?.model_id,
                    )?.name ?? t("No model selected")
                  }
                />
              ) : (
                t("Markdown · inherits this Agent's model")
              )
            }
          >
            <Button
              variant="ghost"
              aria-label={t("Remove subagent {{name}}", {
                name: item.agent ?? item.markdown,
              })}
              onClick={() => remove("subagents", index)}
            >
              <Trash aria-hidden="true" />
              {t("Remove")}
            </Button>
          </SettingsRow>
        ))}
        <ResourceChoice
          loading={sources.isPending}
          label={t("Add subagent")}
          value=""
          onValueChange={(child) => {
            if (child) {
              const [kind, id] = child.split(":");
              add("subagents", { [kind]: id });
            }
          }}
          options={[
            {
              value: "",
              label: t("Choose subagent"),
            },
            ...(sources.data?.sources ?? [])
              .filter(
                (item) =>
                  item.resource_kind === "agent" ||
                  item.resource_kind === "subagent",
              )
              .flatMap((item) =>
                item.resource_ids
                  .filter(
                    (id) =>
                      id !== raw.id &&
                      !children.some(
                        (child) => (child.agent ?? child.markdown) === id,
                      ),
                  )
                  .map((id) => ({
                    value: `${item.resource_kind === "agent" ? "agent" : "markdown"}:${id}`,
                    label:
                      item.resource_kind === "agent"
                        ? t("Agent · {{name}}", { name: agentName(id) })
                        : `Markdown · ${id}`,
                  })),
              ),
          ]}
        />
        {!!root.configuration?.subagents?.include?.length && (
          <div className={styles.sectionNote}>
            <h3>{t("Instance roles")}</h3>
            <p>{root.configuration.subagents.include.join(", ")}</p>
            <small>
              {t("Appended when this Agent runs as a root.")}{" "}
              <Link
                to={`/settings/source?path=${encodeURIComponent(root.path!)}`}
              >
                {t("Manage instance roles")}
              </Link>
            </small>
          </div>
        )}
        {root.error && (
          <div className={styles.sectionNote}>
            <ErrorNotice error={root.error} />
          </div>
        )}
      </SettingsSection>
      <SettingsSection title={t("Advanced tools")}>
        <details className={layout.groupDisclosure}>
          <summary>{t("Tool restrictions")}</summary>
          <div className={styles.stack}>
            <SettingsToggle
              label={t("Restrict tool names")}
              description={t("Allow only the optional tools named below.")}
              checked={Array.isArray(raw.tools)}
              onCheckedChange={(checked) =>
                onChange(
                  updateDocument(source, ["tools"], checked ? [] : undefined),
                )
              }
            />
            {Array.isArray(raw.tools) && (
              <FormField
                label={t("Allowed tool names")}
                description={t(
                  "One tool name per line. Leave empty to disable optional tools. Required tools stay available.",
                )}
              >
                <Textarea
                  variant="soft"
                  rows={3}
                  onBlur={(event) =>
                    onChange(
                      updateDocument(
                        source,
                        ["tools"],
                        event.target.value.split("\n").filter(Boolean),
                      ),
                    )
                  }
                  value={raw.tools.join("\n")}
                  onChange={(event) =>
                    onChange(
                      updateDocument(
                        source,
                        ["tools"],
                        event.target.value
                          ? event.target.value.split("\n")
                          : [],
                      ),
                    )
                  }
                />
              </FormField>
            )}
          </div>
        </details>
        <details className={layout.groupDisclosure}>
          <summary>{t("Saved tool groups")}</summary>
          <div className={styles.stack}>
            <p>
              {t("Shows saved groups. Edit them in Advanced configuration.")}
            </p>
            {!accepted ? (
              <p>{t("Save this agent to inspect its tool groups.")}</p>
            ) : proxy.isPending ? (
              <p>{t("Loading composition…")}</p>
            ) : (
              <>
                {!proxy.data?.sources.length && (
                  <p>{t("No configured plugin or MCP sources.")}</p>
                )}
                {proxy.data?.sources.map((item) => (
                  <p key={item.resource_id}>
                    <span>{item.name}</span> · {item.presentation}
                    {item.group ? ` · ${item.group}` : ""}
                  </p>
                ))}
              </>
            )}
          </div>
        </details>
      </SettingsSection>
    </>
  );
}

function CapabilityOptions({
  source,
  index,
  onChange,
}: {
  source: string;
  index: number;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const document = readDocument(source)!;
  const item = document.toJS().capabilities[index];
  const config = item.configuration ?? {};
  const set = (path: string[], value: unknown) => {
    document.setIn(["capabilities", index, "configuration", ...path], value);
    onChange(document.toString());
  };
  if (item.capability === "skills")
    return (
      <FormField
        label={t("Additional skill directories")}
        description={t(
          "One folder per line. Adds to automatically found skills.",
        )}
      >
        <Textarea
          variant="soft"
          rows={2}
          onBlur={(event) =>
            set(["roots"], event.target.value.split("\n").filter(Boolean))
          }
          value={(config.roots ?? []).join("\n")}
          onChange={(event) =>
            set(
              ["roots"],
              event.target.value ? event.target.value.split("\n") : [],
            )
          }
        />
      </FormField>
    );
  if (item.capability === "web")
    return (
      <details className={styles.details}>
        <summary>
          <span className={management.inlineLabel}>{t("Web options")}</span>
        </summary>
        <div className={styles.stack}>
          <ChoiceField
            label={t("Web search")}
            value={config.search?.mode ?? "auto"}
            options={[
              { value: "auto", label: t("Automatic") },
              { value: "host", label: t("Host search") },
              { value: "native", label: t("Model-native search") },
              { value: "off", label: t("Off") },
            ]}
            onValueChange={(mode) => set(["search", "mode"], mode)}
          />
          <SettingsToggle
            label={t("Fetch web resources")}
            checked={config.fetch?.enabled ?? true}
            onCheckedChange={(checked) => set(["fetch", "enabled"], checked)}
          />
          <SettingsToggle
            label={t("Download files")}
            checked={config.download?.enabled ?? true}
            onCheckedChange={(checked) => set(["download", "enabled"], checked)}
          />
        </div>
      </details>
    );
  return null;
}
