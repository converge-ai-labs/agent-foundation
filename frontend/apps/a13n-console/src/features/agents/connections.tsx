import { CaretDownIcon } from "@phosphor-icons/react";
import {
  BrandIcon,
  Button,
  Checkbox,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  Label,
  Spinner,
} from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import type { AgentConfig } from "./configuration";
import { connectionState } from "../connections/api";
import { MCPConnectionIcon } from "../connections/mcp-icon";
import { ToolPermissions, type PermissionChoice } from "./tool-permissions";
import styles from "./agents.module.css";

type Connections = NonNullable<AgentConfig["connection_tools"]>;
type Selection = Connections[number];
type Connection = Schema["Connection"];
type CatalogTool = { name: string; description: string; unavailable?: boolean };

export function displayName(connection: Connection) {
  const suffix = ` · ${connection.status}`;
  return connection.name.endsWith(suffix)
    ? connection.name.slice(0, -suffix.length)
    : connection.name;
}

export function ConnectionBrandIcon({
  connection,
}: {
  connection: Connection;
}) {
  return (
    <span className={styles.connectionBrandIcon} aria-hidden="true">
      {"app" in connection.config ? (
        <BrandIcon alias={connection.config.app} size={18} />
      ) : (
        <MCPConnectionIcon endpoint={connection.config.url} size={18} />
      )}
    </span>
  );
}

export function ConnectionGroup({
  connection,
  selection,
  readOnly,
  onChange,
  onRemove,
}: {
  connection?: Connection;
  selection: Selection;
  readOnly: boolean;
  onChange: (change: (selection: Selection) => Selection) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(40);
  const catalog = useQuery({
    queryKey: ["connection-tool-catalog", connection?.id, connection?.version],
    enabled: !!connection,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: async ({ signal }): Promise<CatalogTool[]> => {
      if (!connection) return [];
      const result = await client
        .workspace(connection.workspace_id)
        .GET("/api/v1/connections/{connection_id}/tools", {
          params: { path: { connection_id: connection.id } },
          signal,
        })
        .then(data);
      return result.items.map((tool) => ({
        name: tool.name,
        description: tool.description ?? "",
      }));
    },
  });
  const discovered = catalog.data ?? [];
  const known = new Set(discovered.map((tool) => tool.name));
  const retained = (selection.tools ?? Object.keys(selection.permissions ?? {}))
    .filter((name) => !known.has(name))
    .map((name) => ({ name, description: "", unavailable: true }));
  const tools = [...discovered, ...retained];
  const selectedCount =
    selection.tools == null
      ? tools.length
      : tools.filter((tool) => selection.tools?.includes(tool.name)).length;
  const count = catalog.data
    ? t("{{count}} of {{total}} on", {
        count: selectedCount,
        total: tools.length,
      })
    : selection.tools == null
      ? t("All tools")
      : t("{{count}} selected", { count: selection.tools.length });
  function toggleTool(name: string, checked: boolean) {
    onChange((current) => {
      const selected =
        current.tools == null ? tools.map((tool) => tool.name) : current.tools;
      const next = checked
        ? [...new Set([...selected, name])]
        : selected.filter((tool) => tool !== name);
      const allDiscovered =
        !!catalog.data && discovered.every((tool) => next.includes(tool.name));
      const allSelected = allDiscovered && next.length === discovered.length;
      return {
        ...current,
        tools: allSelected ? null : next,
        permissions:
          allSelected || !current.permissions
            ? current.permissions
            : Object.fromEntries(
                Object.entries(current.permissions ?? {}).filter(([tool]) =>
                  next.includes(tool),
                ),
              ),
      };
    });
  }
  function setPermission(name: string, permission: PermissionChoice) {
    onChange((current) => {
      const permissions = { ...current.permissions };
      if (
        permission ===
        (current.permission === "inherit" || !current.permission
          ? "allow"
          : current.permission)
      )
        delete permissions[name];
      else permissions[name] = permission;
      return { ...current, permissions };
    });
  }
  const state = connection && connectionState(connection);
  const editingDisabled = readOnly || state !== "ready";
  return (
    <section className={styles.toolsetGroup}>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <div className={styles.toolsetGroupHeader}>
          <Label className={styles.toolsetGroupSwitch}>
            <Checkbox
              aria-label={t("Enable {{group}} tools", {
                group: connection
                  ? displayName(connection)
                  : selection.connection_id,
              })}
              disabled={editingDisabled}
              checked={selection.tools == null || selection.tools.length > 0}
              onCheckedChange={(checked) =>
                onChange((current) => ({
                  ...current,
                  tools: checked === true ? null : [],
                  permissions: checked === true ? current.permissions : {},
                }))
              }
            />
          </Label>
          <CollapsibleTrigger
            render={
              <button type="button" className={styles.connectionNameTrigger} />
            }
          >
            {connection && <ConnectionBrandIcon connection={connection} />}
            <span
              className={styles.toolsetGroupName}
              title={
                connection ? displayName(connection) : selection.connection_id
              }
            >
              {connection ? displayName(connection) : selection.connection_id}
            </span>
          </CollapsibleTrigger>
          <CollapsibleTrigger
            render={
              <button
                type="button"
                className={styles.connectionSummaryTrigger}
              />
            }
          >
            {state !== "ready" && (
              <span className={styles.toolsetGroupCount}>
                {state ? t(state) : t("Unavailable")}
              </span>
            )}
            {catalog.isPending && connection ? (
              <Spinner size={16} aria-label={t("Loading…")} />
            ) : (
              <span className={styles.toolsetGroupCount}>{count}</span>
            )}
            <CaretDownIcon
              size={16}
              className={
                expanded ? styles.toolsetCaretOpen : styles.toolsetCaret
              }
              aria-hidden="true"
            />
          </CollapsibleTrigger>
          {!readOnly && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t("Remove {{name}}", {
                name: connection
                  ? displayName(connection)
                  : selection.connection_id,
              })}
              onClick={onRemove}
            >
              {t("Remove")}
            </Button>
          )}
        </div>
        <CollapsiblePanel>
          <div className={styles.toolsetGroupBody}>
            <div className={styles.connectionOptions}>
              <div className={styles.connectionOption}>
                <span>
                  <strong>{t("Load tools on demand")}</strong>
                  <small>
                    {t(
                      "Load this Connection's tools only when the agent needs them.",
                    )}
                  </small>
                </span>
                <Checkbox
                  aria-label={t("Load tools on demand")}
                  disabled={editingDisabled}
                  checked={selection.defer_loading ?? false}
                  onCheckedChange={(checked) =>
                    onChange((current) => ({
                      ...current,
                      defer_loading: checked === true,
                    }))
                  }
                />
              </div>
              <div className={styles.connectionOption}>
                <span>
                  <strong>{t("Default tool permission")}</strong>
                  <small>
                    {t(
                      "Applies to tools without an individual permission, including newly discovered tools.",
                    )}
                  </small>
                </span>
                <ToolPermissions
                  name={t("Default tool permission")}
                  label={t("Default tool permission")}
                  value={selection.permission}
                  readOnly={editingDisabled}
                  onChange={(permission) =>
                    onChange((current) => ({ ...current, permission }))
                  }
                />
              </div>
              {selection.permission === "review" && (
                <p className={styles.toolsetToolHint}>
                  {t(
                    "Review is configured as the default. Choose another permission to replace it.",
                  )}
                </p>
              )}
            </div>
            {catalog.isPending && connection && (
              <div className={styles.connectionCatalogStatus}>
                <Loading variant="list" rows={3} />
              </div>
            )}
            {catalog.error && (
              <div className={styles.connectionCatalogStatus}>
                <ErrorNotice error={catalog.error} />
              </div>
            )}
            {!connection && (
              <p className={styles.connectionCatalogStatus}>
                {t(
                  "This Connection is unavailable. Saved tool selections are retained.",
                )}
              </p>
            )}
            {tools.slice(0, limit).map((tool) => {
              const checked =
                selection.tools == null || selection.tools.includes(tool.name);
              const permission =
                selection.permissions?.[tool.name] ??
                selection.permission ??
                "inherit";
              return (
                <div className={styles.toolsetTool} key={tool.name}>
                  <div className={styles.toolsetToolMain}>
                    <Label className={styles.toolsetToolName}>
                      <Checkbox
                        disabled={editingDisabled}
                        checked={checked}
                        onCheckedChange={(value) =>
                          toggleTool(tool.name, value === true)
                        }
                      />
                      <span title={tool.name}>{tool.name}</span>
                    </Label>
                    <span className={styles.toolsetToolDescription}>
                      {tool.unavailable
                        ? t("Not in the current tool catalog")
                        : tool.description}
                    </span>
                    <div className={styles.toolsetToolActions}>
                      <ToolPermissions
                        name={tool.name}
                        value={permission}
                        readOnly={editingDisabled}
                        onChange={(value) => setPermission(tool.name, value)}
                      />
                    </div>
                  </div>
                  {permission === "review" && (
                    <p className={styles.toolsetToolHint}>
                      {t(
                        "Review is configured for this tool. Choose another permission to replace it.",
                      )}
                    </p>
                  )}
                </div>
              );
            })}
            {tools.length > limit && (
              <div className={styles.connectionCatalogStatus}>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setLimit((value) => value + 40)}
                >
                  {t("Show more tools")}
                </Button>
              </div>
            )}
            {catalog.data && !tools.length && (
              <p className={styles.connectionCatalogStatus}>
                {t("No tools found")}
              </p>
            )}
          </div>
        </CollapsiblePanel>
      </Collapsible>
    </section>
  );
}
