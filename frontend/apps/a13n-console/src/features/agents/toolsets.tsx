import {
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  Label,
  Checkbox,
} from "a13n-ui";
import { CaretDownIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { webProviderApi } from "../web/api";
import { providersPath } from "../providers/navigation";
import { Section } from "../../shared/page";
import type { AgentConfig } from "./configuration";
import { toolState } from "./toolset-state";
import { ToolPermissions } from "./tool-permissions";
import { WebToolSettings, WebLocalSettings } from "./web-tool-settings";
import styles from "./agents.module.css";

type Toolsets = NonNullable<AgentConfig["toolsets"]>;
type Definition = Schema["ToolsetDefinition"];
const names: Record<Definition["key"], string> = {
  files: "Files",
  shell: "Terminal",
  web: "Web",
  assets: "Assets",
};
const groupDescriptions: Record<Definition["key"], string> = {
  files: "Read, write, and organize files.",
  shell: "Run commands and manage processes.",
  web: "Search and retrieve online content.",
  assets: "Publish files as agent assets.",
};
const toolDescriptions: Record<string, Record<string, string>> = {
  files: {
    view: "Read file contents",
    write: "Create or replace a file",
    edit: "Edit a file",
    multi_edit: "Apply several file edits",
    mkdir: "Create directories",
    move: "Move a file or directory",
    copy: "Copy a file or directory",
    delete: "Delete a file or directory",
    ls: "List directory contents",
    glob: "Find matching paths",
    grep: "Search file contents",
  },
  shell: {
    exec: "Execute a command",
    info: "Inspect a running process",
    wait: "Wait for a process",
    input: "Send input to a process",
    signal: "Signal a process",
  },
  web: {
    search: "Search the web",
    scrape: "Extract page content",
    fetch: "Fetch URL content",
    download: "Download a resource",
  },
  assets: { publish: "Publish an asset" },
};

export function AgentToolsets({
  value,
  onChange,
  reviewer,
  readOnly = false,
}: {
  value: Toolsets;
  onChange: Dispatch<SetStateAction<Toolsets>>;
  reviewer: AgentConfig["reviewer"];
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const [selected, setSelected] = useState<Definition["key"] | null>(null);
  const catalog = useQuery({
    queryKey: ["toolset-catalog", workspace.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/toolsets", {
          params: { path: { workspace: workspace.id } },
          signal,
        })
        .then(data),
  });
  const webProviders = useQuery({
    queryKey: ["web-providers", workspace.id, "choices"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        webProviderApi(client, {
          kind: "workspace",
          id: workspace.id,
        }).providers(signal, cursor),
      ),
  });
  const webProviderTypes = useQuery({
    queryKey: ["web-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/web-provider-types", { signal }).then(data),
  });
  const providers = webProviders.data ?? [];
  const providerTypes = webProviderTypes.data?.items ?? [];
  const [candidate, setCandidate] = useState<{
    toolsets: Toolsets;
    reviewer: AgentConfig["reviewer"];
  } | null>(null);
  useEffect(() => {
    if (readOnly || !catalog.data) return;
    const timer = window.setTimeout(
      () => setCandidate({ toolsets: value, reviewer }),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [value, reviewer, readOnly, catalog.data]);
  const validation = useQuery({
    queryKey: ["toolset-candidate", workspace.id, candidate],
    enabled: !!candidate,
    queryFn: ({ signal }) =>
      client.http
        .POST("/api/v1/workspaces/{workspace}/toolsets/validate", {
          params: { path: { workspace: workspace.id } },
          body: candidate!,
          signal,
        })
        .then(data),
  });
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
          [tool]: { ...previous[group]?.tools?.[tool], ...patch },
        },
      },
    }));
  }
  return (
    <Section
      title={t("Tools")}
      description={t(
        "Choose the tools this agent can use and set access for each action.",
      )}
    >
      <div className={styles.toolsetCard}>
        {catalog.data?.items.map((group) => {
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
                        group: t(names[group.key]),
                      })}
                    </span>
                  </Label>
                  <CollapsibleTrigger
                    render={
                      <button
                        type="button"
                        className={styles.toolsetGroupTrigger}
                      />
                    }
                  >
                    <span className={styles.toolsetGroupName}>
                      {t(names[group.key])}
                    </span>
                    <span className={styles.toolsetGroupDescription}>
                      {t(groupDescriptions[group.key])}
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
                        selection?.permission === "inherit"
                          ? "allow"
                          : (selection?.permission ?? "allow");
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
                                  disabled={
                                    readOnly || !enabled || providerMissing
                                  }
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
                                <span>{tool.model_name}</span>
                              </Label>
                              {providerHint && (
                                <span
                                  id={`web-provider-${tool.key}-hint`}
                                  className="sr-only"
                                >
                                  {providerHint}
                                </span>
                              )}
                              <span className={styles.toolsetToolDescription}>
                                {t(
                                  toolDescriptions[group.key]?.[tool.key] ??
                                    tool.model_name,
                                )}
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
                                    <CaretDownIcon
                                      size={14}
                                      aria-hidden="true"
                                    />
                                  </CollapsibleTrigger>
                                )}
                                <ToolPermissions
                                  name={tool.model_name}
                                  value={selectedPermission}
                                  supported={tool.supported_permissions}
                                  readOnly={readOnly}
                                  onChange={(permission) =>
                                    updateTool(group.key, tool.key, {
                                      permission,
                                    })
                                  }
                                />
                              </div>
                            </div>
                            {selectedPermission === "review" && (
                              <p className={styles.toolsetToolHint}>
                                {t(
                                  "Review is configured for this tool. Choose another permission to replace it.",
                                )}
                              </p>
                            )}
                            {group.key === "web" && (
                              <CollapsiblePanel>
                                <div className={styles.webConfigGrid}>
                                  {tool.resource_selector ? (
                                    <WebToolSettings
                                      operation={
                                        tool.resource_selector.operation
                                      }
                                      providers={providers}
                                      types={providerTypes}
                                      providerError={
                                        webProviders.error ??
                                        webProviderTypes.error
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
        })}
      </div>
      <ErrorNotice error={catalog.error} />
      {validation.error && <ErrorNotice error={validation.error} />}
      {candidate &&
        JSON.stringify(candidate) ===
          JSON.stringify({ toolsets: value, reviewer }) &&
        validation.data?.errors.map((error, index) => (
          <p
            role="alert"
            key={`${error.path}-${index}`}
            className="text-sm text-destructive"
          >
            {error.path}: {t(error.code)}
            {error.setup_destination?.kind === "web_provider" && (
              <>
                {" "}
                ·{" "}
                <a
                  href={providersPath("web", "workspace", workspace.key)}
                  className="underline"
                >
                  {t("Manage Web Providers")}
                </a>
              </>
            )}
            {error.setup_destination?.kind === "reviewer" && (
              <>
                {" "}
                ·{" "}
                <a
                  href={`/workspace/${encodeURIComponent(workspace.key)}/models`}
                  className="underline"
                >
                  {t("Manage models")}
                </a>
              </>
            )}
          </p>
        ))}
    </Section>
  );
}
