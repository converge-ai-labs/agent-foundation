import {
  Button,
  DisclosureSection,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  Label,
  Checkbox,
} from "a13n-ui";
import { CaretDownIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import {
  useEffect,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { webProviderApi } from "../web/api";
import { providersPath } from "../providers/navigation";
import { Section } from "../../shared/page";
import type { ReviewTarget } from "./reviewer";
import type { AgentConfig } from "./configuration";
import { toolState } from "./toolset-state";
import { ToolPermissions } from "./tool-permissions";
import { WebToolSettings, WebLocalSettings } from "./web-tool-settings";
import styles from "./agents.module.css";

type Toolsets = NonNullable<AgentConfig["toolsets"]>;
type Definition = Schema["ToolsetDefinition"];

/** The built-in and platform toolsets this deployment offers. */
export function useToolsetCatalog() {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["toolset-catalog", workspace.id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/toolsets", {
          signal,
        })
        .then(data),
  });
}

export function AgentToolsets({
  value,
  onChange,
  config,
  agentId,
  readOnly = false,
  mark,
  onReview,
  onRequireReviewer,
}: {
  onReview?: (target: ReviewTarget) => void;
  onRequireReviewer?: () => void;
  value: Toolsets;
  onChange: Dispatch<SetStateAction<Toolsets>>;
  /** The whole configuration a save would publish, once the draft builds. */
  config?: AgentConfig;
  /** The agent the configuration would become a version of; none while creating. */
  agentId?: string;
  readOnly?: boolean;
  /** Says how a read-only view's value differs, beside the title. */
  mark?: ReactNode;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, basePath } = useWorkspace();
  const [selected, setSelected] = useState<Definition["key"] | null>(null);
  const catalog = useToolsetCatalog();
  const webProviders = useQuery({
    queryKey: ["web-providers", workspace.id, "choices"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        webProviderApi(client, workspace.id).providers(signal, cursor),
      ),
  });
  const webProviderTypes = useQuery({
    queryKey: ["web-provider-types"],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: "web" } },
          signal,
        })
        .then(data),
  });
  const providers = webProviders.data ?? [];
  const providerTypes = webProviderTypes.data?.items ?? [];
  // The draft rebuilds its configuration on every render; the text identifies it.
  const configText = config?.model ? JSON.stringify(config) : undefined;
  const [candidate, setCandidate] = useState<{
    text: string;
    config: AgentConfig;
  } | null>(null);
  useEffect(() => {
    if (readOnly || !catalog.data || !config || !configText) return;
    const timer = window.setTimeout(
      () => setCandidate({ text: configText, config }),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [configText, readOnly, catalog.data]);
  const validation = useQuery({
    queryKey: [
      "agent-config-validation",
      workspace.id,
      agentId,
      candidate?.text,
    ],
    enabled: !!candidate,
    retry: false,
    queryFn: async ({ signal }) => {
      if (candidate)
        await client.workspace(workspace.id).POST("/api/v1/agents/validate", {
          body: { config: candidate.config, agent_id: agentId },
          signal,
        });
      return null;
    },
  });
  // A refusal names the field it concerns; any other failure is a request error.
  const refusal =
    validation.error instanceof ApiError &&
    validation.error.code === "invalid_argument"
      ? validation.error
      : undefined;
  const refusedField =
    typeof refusal?.details.field === "string" ? refusal.details.field : "";
  function updateGroup(group: Definition, enabled: boolean) {
    onChange((previous) => ({
      ...previous,
      [group.key]: {
        ...previous[group.key],
        enabled,
        tools: Object.fromEntries(
          group.tools.map((tool) => {
            const selection = previous[group.key]?.tools?.[tool.key];
            const { provider, providerMissing } = toolState(
              true,
              tool,
              selection,
              providers,
              providerTypes,
            );
            return [
              tool.key,
              {
                permission: tool.default_permission,
                ...selection,
                enabled: enabled && !providerMissing,
                ...(provider && {
                  config: { ...selection?.config, provider_id: provider.id },
                }),
              },
            ];
          }),
        ),
      },
    }));
  }
  function updateTool(
    group: string,
    tool: string,
    patch: Partial<Schema["ToolSelection"]>,
  ) {
    onChange((previous) => ({
      ...previous,
      [group]: {
        ...previous[group],
        tools: {
          ...previous[group]?.tools,
          [tool]: {
            permission:
              catalog.data?.items
                .find((item) => item.key === group)
                ?.tools.find((item) => item.key === tool)?.default_permission ??
              "inherit",
            ...previous[group]?.tools?.[tool],
            ...patch,
          },
        },
      },
    }));
  }
  const renderGroup = (group: Definition) => {
    const groupLabel = t(group.display_name);
    const enabled = value[group.key]?.enabled ?? group.default_enabled;
    const expanded = selected === group.key;
    const activeCount = group.tools.filter(
      (tool) =>
        toolState(
          enabled,
          tool,
          value[group.key]?.tools?.[tool.key],
          providers,
          providerTypes,
        ).enabled,
    ).length;
    return (
      <section className={styles.toolsetGroup} key={group.key}>
        <Collapsible
          open={expanded}
          onOpenChange={(open) => setSelected(open ? group.key : null)}
        >
          <div className={styles.toolsetGroupHeader}>
            <Label className={styles.toolsetGroupSwitch}>
              <Checkbox
                disabled={readOnly}
                checked={enabled}
                onCheckedChange={(checked) =>
                  updateGroup(group, checked === true)
                }
              />
              <span className="sr-only">
                {t("Enable {{group}} tools", {
                  group: groupLabel,
                })}
              </span>
            </Label>
            <CollapsibleTrigger
              render={
                <button type="button" className={styles.toolsetGroupTrigger} />
              }
            >
              <span className={styles.toolsetGroupName}>{groupLabel}</span>
              <span
                className={styles.toolsetGroupDescription}
                title={group.description ? t(group.description) : undefined}
              >
                {group.description && t(group.description)}
              </span>
              <span className={styles.toolsetGroupCount}>
                {enabled
                  ? t("{{count}} of {{total}} on", {
                      count: activeCount,
                      total: group.tools.length,
                    })
                  : t("Off")}
              </span>
              <CaretDownIcon
                size={16}
                className={
                  expanded ? styles.toolsetCaretOpen : styles.toolsetCaret
                }
                aria-hidden="true"
              />
            </CollapsibleTrigger>
          </div>
          <CollapsiblePanel>
            <div className={styles.toolsetGroupBody}>
              {group.tools.map((tool) => {
                const selection = value[group.key]?.tools?.[tool.key];
                const selectedPermission =
                  !selection?.permission || selection.permission === "inherit"
                    ? tool.default_permission
                    : selection.permission;
                const {
                  provider,
                  providerMissing,
                  enabled: toolEnabled,
                } = toolState(
                  enabled,
                  tool,
                  selection,
                  providers,
                  providerTypes,
                );
                const providerHint = providerMissing
                  ? webProviders.isPending || webProviderTypes.isPending
                    ? t("Loading Web Providers…")
                    : webProviders.error || webProviderTypes.error
                      ? t("Web Providers could not be loaded.")
                      : selection?.config?.provider_id
                        ? t(
                            "The selected Web Provider is unavailable. Choose another provider to enable this tool.",
                          )
                        : t(
                            "Add an enabled, configured Web Provider to enable this tool.",
                          )
                  : undefined;
                return (
                  <div
                    className={`${styles.toolsetTool} ${group.key === "web" ? styles.toolsetToolWeb : ""}`}
                    key={tool.key}
                  >
                    <Collapsible>
                      <div className={styles.toolsetToolMain}>
                        <Label
                          className={styles.toolsetToolName}
                          title={!toolEnabled ? providerHint : undefined}
                        >
                          <Checkbox
                            aria-describedby={
                              providerHint
                                ? `web-provider-${tool.key}-hint`
                                : undefined
                            }
                            disabled={readOnly || !enabled || providerMissing}
                            checked={toolEnabled}
                            onCheckedChange={(checked) =>
                              updateTool(group.key, tool.key, {
                                enabled: checked === true,
                                ...(checked === true &&
                                  provider && {
                                    config: {
                                      ...selection?.config,
                                      provider_id: provider.id,
                                    },
                                  }),
                              })
                            }
                          />
                          <span title={tool.model_name}>{tool.model_name}</span>
                        </Label>
                        {providerHint && (
                          <span
                            id={`web-provider-${tool.key}-hint`}
                            className="sr-only"
                          >
                            {providerHint}
                          </span>
                        )}
                        <span
                          className={styles.toolsetToolDescription}
                          title={t(tool.description || tool.display_name)}
                        >
                          {t(tool.description || tool.display_name)}
                        </span>
                        <div className={styles.toolsetToolActions}>
                          {group.key === "web" && (
                            <CollapsibleTrigger
                              render={
                                <button
                                  type="button"
                                  className={styles.webConfigTrigger}
                                  aria-label={t("Configure {{tool}}", {
                                    tool: tool.model_name,
                                  })}
                                />
                              }
                            >
                              {t("Configure")}
                              <CaretDownIcon size={14} aria-hidden="true" />
                            </CollapsibleTrigger>
                          )}
                          <ToolPermissions
                            name={tool.model_name}
                            value={selectedPermission}
                            supported={tool.supported_permissions}
                            readOnly={readOnly}
                            onChange={(permission) => {
                              updateTool(group.key, tool.key, { permission });
                              if (permission === "review")
                                onRequireReviewer?.();
                            }}
                          />
                        </div>
                      </div>
                      {selectedPermission === "review" && (
                        <p className={styles.toolsetToolHint}>
                          {t("This tool uses automatic review.")}
                          {onReview && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                onReview({
                                  selector: tool.execution_id,
                                  label: tool.model_name,
                                })
                              }
                            >
                              {t("Tool review rule")}
                            </Button>
                          )}
                        </p>
                      )}
                      {group.key === "web" && (
                        <CollapsiblePanel>
                          <div className={styles.webConfigGrid}>
                            {tool.resource_selector ? (
                              <WebToolSettings
                                operation={tool.resource_selector.operation}
                                providers={providers}
                                types={providerTypes}
                                providerError={
                                  webProviders.error ?? webProviderTypes.error
                                }
                                config={selection?.config ?? {}}
                                reuseProviderId={
                                  value.web?.tools?.[
                                    tool.resource_selector.operation ===
                                    "search"
                                      ? "scrape"
                                      : "search"
                                  ]?.config?.provider_id
                                }
                                readOnly={readOnly}
                                onChange={(config) =>
                                  updateTool(group.key, tool.key, {
                                    config,
                                  })
                                }
                              />
                            ) : (
                              <WebLocalSettings
                                tool={tool.key as "fetch" | "download"}
                                config={selection?.config ?? {}}
                                readOnly={readOnly}
                                onChange={(config) =>
                                  updateTool(group.key, tool.key, {
                                    config,
                                  })
                                }
                              />
                            )}
                          </div>
                        </CollapsiblePanel>
                      )}
                    </Collapsible>
                  </div>
                );
              })}
            </div>
          </CollapsiblePanel>
        </Collapsible>
      </section>
    );
  };
  const groups = catalog.data?.items ?? [];
  const platform = groups.filter((group) => group.platform);
  return (
    <Section
      title={t("Tools")}
      description={t(
        "Choose the tools this agent can use and set access for each action.",
      )}
      actions={mark}
    >
      <div className={styles.toolsetCard}>
        {groups.filter((group) => !group.platform).map(renderGroup)}
      </div>
      {platform.length > 0 && (
        <DisclosureSection
          title={t("Advanced / Platform Features")}
          defaultOpen={false}
        >
          <div className={`${styles.toolsetCard} ${styles.toolsetCardNested}`}>
            {platform.map(renderGroup)}
          </div>
        </DisclosureSection>
      )}
      <ErrorNotice error={catalog.error} />
      {validation.error && !refusal && <ErrorNotice error={validation.error} />}
      {refusal && candidate?.text === configText && (
        <p role="alert" className="text-sm text-destructive">
          {refusal.message}
          {refusedField.startsWith("toolsets.web") && (
            <>
              {" "}
              ·{" "}
              <a href={providersPath("web", workspace)} className="underline">
                {t("Manage Web Providers")}
              </a>
            </>
          )}
          {/^(model|reviewer|media_understanding)\b/.test(refusedField) && (
            <>
              {" "}
              ·{" "}
              <a href={`${basePath}/models`} className="underline">
                {t("Manage models")}
              </a>
            </>
          )}
        </p>
      )}
    </Section>
  );
}
