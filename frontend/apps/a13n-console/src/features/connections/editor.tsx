import { ConfigurationSummary } from "../../shared/configuration-summary";
import { DotsThreeIcon } from "@phosphor-icons/react";
import {
  BrandIcon,
  Button,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  SettingsRow,
  SettingsSection,
  Tabs,
  TabsList,
  TabsTab,
} from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId, IconTile } from "../../shared/identity";
import { Panel } from "../../shared/page";
import { ConnectionSetup } from "../connectors/setup";
import { MCPAuthorization } from "../mcp/authorization";
import { MCPTools } from "../mcp/tools";
import { MCPConnectionIcon } from "./mcp-icon";
import {
  connectionState,
  revokeCleanup,
  testConnection,
  type RemoteCleanup,
} from "./api";
import styles from "./connections.module.css";

/** One connection, inspected beside the collection instead of over it. */
export function ConnectionDetails({
  connectionId,
  onCleanup,
  onClose,
}: {
  connectionId: string;
  onCleanup: (cleanup: RemoteCleanup) => void;
  onClose: () => void;
}) {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["connections", workspace.id, connectionId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/connections/{connection_id}", {
          params: {
            path: { connection_id: connectionId },
          },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  const connection = query.data;
  const manage = can("write");
  const mcp = connection?.type === "mcp";
  const tabNames = manage
    ? ["details", "setup", ...(mcp ? ["tools"] : [])]
    : ["details"];
  const [tab, setTab] = useState<string>();
  const active =
    tab && tabNames.includes(tab)
      ? tab
      : connection &&
          ["pending", "reauthorization_required"].includes(connection.status)
        ? "setup"
        : "details";
  return (
    <Panel
      open
      onClose={onClose}
      label={t("Connection details")}
      title={
        <>
          <IconTile size={32} tone="surface">
            {connection && "url" in connection.config ? (
              <MCPConnectionIcon endpoint={connection.config.url} />
            ) : (
              <BrandIcon
                alias={
                  connection && "app" in connection.config
                    ? connection.config.app
                    : undefined
                }
              />
            )}
          </IconTile>
          <strong title={connection?.name}>
            {connection?.name ?? t("Connection")}
          </strong>
          <StatePill
            state={connection ? connectionState(connection) : "pending"}
          />
        </>
      }
      actions={
        connection &&
        manage && (
          <ConnectionMenu
            connection={connection}
            onCleanup={onCleanup}
            onDone={onClose}
          />
        )
      }
      tabs={
        connection && tabNames.length > 1 ? (
          <Tabs
            className={styles.panelTabs}
            value={active}
            onValueChange={(value) => setTab(String(value))}
          >
            <TabsList variant="underline" aria-label={t("Connection details")}>
              <TabsTab value="details">{t("Details")}</TabsTab>
              <TabsTab value="setup">{t("Authorization")}</TabsTab>
              {mcp && <TabsTab value="tools">{t("Tools")}</TabsTab>}
            </TabsList>
          </Tabs>
        ) : undefined
      }
    >
      {query.isPending ? (
        <Loading variant="form" rows={4} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : (
        connection && (
          <div className={styles.panelBody} key={generation}>
            {!manage ? (
              <ConfigurationSummary value={connection.config} />
            ) : (
              <>
                {/* Details stays mounted so an unsaved name survives a tab visit. */}
                <div hidden={active !== "details"} className={styles.stack}>
                  <ConnectionSettings connection={connection} reload={reload} />
                </div>
                {active === "setup" &&
                  (connection.type !== "mcp" ? (
                    <ConnectionSetup connection={connection} />
                  ) : (
                    <MCPAuthorization
                      initial={connection}
                      reload={reload}
                      onConnectionChange={() => void reload()}
                    />
                  ))}
                {active === "tools" && <MCPTools connection={connection} />}
              </>
            )}
          </div>
        )
      )}
    </Panel>
  );
}

/** Availability and removal for one connection. */
function ConnectionMenu({
  connection,
  onCleanup,
  onDone,
}: {
  connection: Schema["Connection"];
  onCleanup: (cleanup: RemoteCleanup) => void;
  onDone: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const http = client.workspace(connection.workspace_id);
  const request = {
    params: { path: { connection_id: connection.id } },
    headers: ifMatch(rowTag(connection)),
  };
  const done = () => {
    void cache.invalidateQueries({ queryKey: ["connections"] });
    onDone();
  };
  const retry = () =>
    void cache.invalidateQueries({ queryKey: ["connections"] });
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            type="button"
            aria-label={t("Connection actions")}
            title={t("Connection actions")}
          />
        }
      >
        <DotsThreeIcon size={16} />
      </MenuTrigger>
      <MenuPopup align="end">
        <Confirm
          subject={connection.name}
          retry={retry}
          title={t(
            connection.enabled ? "Disable connection" : "Enable connection",
          )}
          description={t(
            "This changes whether new agent calls can use the connection.",
          )}
          triggerElement={
            <MenuItem closeOnClick={false}>
              {t(connection.enabled ? "Disable" : "Enable")}
            </MenuItem>
          }
          action={async () => {
            data(
              await http.PATCH("/api/v1/connections/{connection_id}", {
                ...request,
                body: { enabled: !connection.enabled },
              }),
            );
            done();
          }}
        />
        {connection.auth !== "none" && (
          <>
            <MenuSeparator />
            <Confirm
              subject={connection.name}
              retry={retry}
              title={t("Revoke authorization")}
              description={t(
                "Local access is disabled immediately. The result reports whether external cleanup succeeded.",
              )}
              triggerElement={
                <MenuItem closeOnClick={false} variant="destructive">
                  {t("Revoke")}
                </MenuItem>
              }
              danger
              action={async () => {
                const revoked = data(
                  await http.POST(
                    "/api/v1/connections/{connection_id}/revoke",
                    request,
                  ),
                );
                onCleanup(revokeCleanup(revoked));
                done();
              }}
            />
          </>
        )}
      </MenuPopup>
    </Menu>
  );
}

/** Settings rows: the name is editable in place; everything else is evidence. */
function ConnectionSettings({
  connection,
  reload,
}: {
  connection: Schema["Connection"];
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    nameId = useId(),
    [name, setName] = useState(connection.name);
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(connection.workspace_id)
        .PATCH("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
          headers: ifMatch(rowTag(connection)),
          body: { name },
        })
        .then(data),
    onSuccess: async () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
      await reload();
    },
  });
  const check = useMutation({
    mutationFn: () => testConnection(client, connection),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  const lastCheck = connection.last_test;
  const changed = name !== connection.name;
  return (
    <>
      <SettingsSection>
        <SettingsRow label={t("Name")} controlId={nameId}>
          <form
            className={styles.inlineEdit}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <Input
              id={nameId}
              size="sm"
              required
              value={name}
              maxLength={128}
              onChange={(event) => setName(event.target.value)}
            />
            {changed && (
              <Button type="submit" size="sm" loading={save.isPending}>
                {t("Save")}
              </Button>
            )}
          </form>
        </SettingsRow>
        <SettingsRow label={t("Source")}>
          {connection.type === "mcp" ? t("Remote MCP") : t("Connected account")}
        </SettingsRow>
        <SettingsRow
          label={t(connection.type === "mcp" ? "Endpoint" : "Connector key")}
        >
          <CopyableId
            value={
              "url" in connection.config
                ? connection.config.url
                : connection.config.app
            }
          />
        </SettingsRow>
        <SettingsRow label={t("Created")}>
          <Timestamp value={connection.created_at} relative />
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <SettingsSection title={t("Connection check")}>
        <SettingsRow
          label={t("Last check")}
          description={
            lastCheck
              ? (lastCheck.message ??
                t(
                  connection.type === "mcp"
                    ? "MCP discovery check"
                    : "Provider account check",
                ))
              : t("No check has run for this connection.")
          }
        >
          <span className={styles.checkRow}>
            {lastCheck && (
              <>
                <StatePill state={lastCheck.status} />
                <Timestamp value={lastCheck.tested_at} relative />
              </>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={check.isPending}
              disabled={!connection.enabled}
              onClick={() => check.mutate()}
            >
              {t("Check connection")}
            </Button>
          </span>
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice error={check.error} />
      {!connection.enabled && (
        <p className={styles.reason}>
          {t(
            "After enabling, check the connection. Open the Authorization tab if new credentials are needed.",
          )}
        </p>
      )}
      <ConfigurationSummary value={connection.config} />
    </>
  );
}
