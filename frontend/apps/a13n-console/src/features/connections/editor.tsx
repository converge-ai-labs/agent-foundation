import {
  DotsThreeOutlineVerticalIcon,
  PowerIcon,
  ProhibitIcon,
} from "@phosphor-icons/react";
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
import { useId, useState, type ReactElement } from "react";
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
              <ConnectionSettings connection={connection} reload={reload} />
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
        <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
      </MenuTrigger>
      <MenuPopup align="end">
        <AvailabilityConfirm
          connection={connection}
          trigger={
            <MenuItem closeOnClick={false}>
              <PowerIcon size={14} />
              {t(connection.enabled ? "Disable" : "Enable")}
            </MenuItem>
          }
        />
        {connection.auth !== "none" && (
          <>
            <MenuSeparator />
            <Confirm
              subject={connection.name}
              retry={() =>
                void cache.invalidateQueries({ queryKey: ["connections"] })
              }
              title={t("Revoke authorization")}
              description={t(
                "Local access is disabled immediately. The result reports whether external cleanup succeeded.",
              )}
              triggerElement={
                <MenuItem closeOnClick={false} variant="destructive">
                  <ProhibitIcon size={14} />
                  {t("Revoke")}
                </MenuItem>
              }
              danger
              action={async () => {
                const revoked = data(
                  await client
                    .workspace(connection.workspace_id)
                    .POST("/api/v1/connections/{connection_id}/revoke", {
                      params: { path: { connection_id: connection.id } },
                      headers: ifMatch(rowTag(connection)),
                    }),
                );
                onCleanup(revokeCleanup(revoked));
                void cache.invalidateQueries({ queryKey: ["connections"] });
                onDone();
              }}
            />
          </>
        )}
      </MenuPopup>
    </Menu>
  );
}

/** Turns a connection on or off once the change is confirmed. */
function AvailabilityConfirm({
  connection,
  trigger,
}: {
  connection: Schema["Connection"];
  trigger: ReactElement;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const refresh = () =>
    void cache.invalidateQueries({ queryKey: ["connections"] });
  return (
    <Confirm
      subject={connection.name}
      retry={refresh}
      title={t(connection.enabled ? "Disable connection" : "Enable connection")}
      description={t(
        "This changes whether new agent calls can use the connection.",
      )}
      triggerElement={trigger}
      action={async () => {
        data(
          await client
            .workspace(connection.workspace_id)
            .PATCH("/api/v1/connections/{connection_id}", {
              params: { path: { connection_id: connection.id } },
              headers: ifMatch(rowTag(connection)),
              body: { enabled: !connection.enabled },
            }),
        );
        refresh();
      }}
    />
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
    { can } = useWorkspace(),
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
  const manage = can("write");
  return (
    <>
      {!connection.enabled && (
        <div className={styles.reason}>
          <p>
            {t(
              "After enabling, check the connection. Open the Authorization tab if new credentials are needed.",
            )}
          </p>
          {manage && (
            <AvailabilityConfirm
              connection={connection}
              trigger={
                <Button type="button" variant="outline" size="sm">
                  {t("Enable")}
                </Button>
              }
            />
          )}
        </div>
      )}
      <SettingsSection variant="outlined">
        <SettingsRow label={t("Name")} controlId={manage ? nameId : undefined}>
          {manage ? (
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
          ) : (
            connection.name
          )}
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
      <SettingsSection variant="outlined" title={t("Connection check")}>
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
            {manage && (
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
            )}
          </span>
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice error={check.error} />
    </>
  );
}
