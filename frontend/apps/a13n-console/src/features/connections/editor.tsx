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
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId, IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { Panel } from "../../shared/page";
import { ConnectionSetup } from "../connectors/setup";
import { MCPAuthorization } from "../mcp/authorization";
import { MCPTools } from "../mcp/tools";
import { MCPConnectionIcon } from "./mcp-icon";
import styles from "./connections.module.css";

/** One connection, inspected beside the collection instead of over it. */
export function ConnectionDetails({
  connectionId,
  onCleanup,
  onClose,
}: {
  connectionId: string;
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  onClose: () => void;
}) {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["connections", workspace.id, connectionId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: connectionId } },
          signal,
        })
        .then(data)
        .then((connection) => {
          if (connection.workspace_id !== workspace.id)
            throw new Error(t("Connection belongs to another workspace."));
          return connection;
        }),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  const connection = query.data;
  const manage = can("connection.manage");
  const mcp = connection?.source.kind === "mcp";
  const tabNames = manage
    ? ["details", "setup", ...(mcp ? ["tools"] : [])]
    : ["details"];
  const [tab, setTab] = useState<string>();
  const active =
    tab && tabNames.includes(tab)
      ? tab
      : connection && ["pending", "action_required"].includes(connection.status)
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
            {connection?.source.kind === "mcp" ? (
              <MCPConnectionIcon endpoint={connection.source.endpoint_url} />
            ) : (
              <BrandIcon
                alias={
                  connection?.source.kind === "connector"
                    ? connection.source.connector_key
                    : undefined
                }
              />
            )}
          </IconTile>
          <strong title={connection?.name}>
            {connection?.name ?? t("Connection")}
          </strong>
          <StatePill state={connection?.status ?? "pending"} />
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
            {connection.status_reason && (
              <p className={styles.reason}>
                {t(`state.${connection.status_reason}`)}
              </p>
            )}
            {!manage ? (
              <ConfigurationSummary value={connection.safe_metadata ?? {}} />
            ) : (
              <>
                {/* Details stays mounted so an unsaved name survives a tab visit. */}
                <div hidden={active !== "details"}>
                  <ConnectionSettings connection={connection} reload={reload} />
                </div>
                {active === "setup" &&
                  (connection.source.kind === "connector" ? (
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
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  onDone: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const body = { expected_version: connection.version };
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
            connection.status === "disabled"
              ? "Enable connection"
              : "Disable connection",
          )}
          description={t(
            "This changes whether new agent calls can use the connection.",
          )}
          triggerElement={
            <MenuItem closeOnClick={false}>
              {t(connection.status === "disabled" ? "Enable" : "Disable")}
            </MenuItem>
          }
          action={async () => {
            const action =
              connection.status === "disabled" ? "enable" : "disable";
            data(
              await client.http.POST(
                action === "enable"
                  ? "/api/v1/connections/{connection_id}/enable"
                  : "/api/v1/connections/{connection_id}/disable",
                {
                  params: {
                    path: { connection_id: connection.id },
                    header: commandHeaders(
                      workspace.id,
                      key.forBody({ action, ...body }),
                    ),
                  },
                  body,
                },
              ),
            );
            done();
          }}
        />
        <MenuSeparator />
        {(connection.source.kind === "connector"
          ? (["revoke", "delete"] as const)
          : (["delete"] as const)
        ).map((action) => (
          <Confirm
            key={action}
            subject={connection.name}
            retry={retry}
            title={t(
              action === "revoke"
                ? "Revoke authorization"
                : "Delete connection",
            )}
            description={t(
              "Local access is disabled immediately. The result reports whether external cleanup succeeded.",
            )}
            triggerElement={
              <MenuItem closeOnClick={false} variant="destructive">
                {t(action === "revoke" ? "Revoke" : "Delete")}
              </MenuItem>
            }
            danger
            action={async () => {
              const header = commandHeaders(
                workspace.id,
                key.forBody({ action, ...body }),
              );
              const result =
                action === "revoke"
                  ? data(
                      await client.http.POST(
                        "/api/v1/connections/{connection_id}/connector/revoke",
                        {
                          params: {
                            path: { connection_id: connection.id },
                            header,
                          },
                          body,
                        },
                      ),
                    )
                  : data(
                      await client.http.DELETE(
                        "/api/v1/connections/{connection_id}",
                        {
                          params: {
                            path: { connection_id: connection.id },
                            header,
                            query: body,
                          },
                        },
                      ),
                    );
              onCleanup(result);
              done();
            }}
          />
        ))}
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
      client.http
        .PATCH("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
          body: { name, expected_version: connection.version },
        })
        .then(data),
    onSuccess: async () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
      await reload();
    },
  });
  const check = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/connections/{connection_id}/check", {
          params: { path: { connection_id: connection.id } },
          body: { expected_version: connection.version },
        })
        .then(data),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
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
          {connection.source.kind === "mcp"
            ? t("Remote MCP")
            : t("Connected account")}
        </SettingsRow>
        <SettingsRow
          label={t(
            connection.source.kind === "mcp" ? "Endpoint" : "Connector key",
          )}
        >
          <CopyableId
            value={
              connection.source.kind === "mcp"
                ? connection.source.endpoint_url
                : connection.source.connector_key
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
            connection.last_check
              ? t(
                  connection.last_check.scope === "provider_account"
                    ? "Provider account check"
                    : "MCP discovery check",
                )
              : t("No check has run for this connection.")
          }
        >
          <span className={styles.checkRow}>
            {connection.last_check && (
              <>
                <StatePill state={connection.last_check.status} />
                <Timestamp value={connection.last_check.checked_at} relative />
              </>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={check.isPending}
              disabled={connection.status === "disabled"}
              onClick={() => check.mutate()}
            >
              {t("Check connection")}
            </Button>
          </span>
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice error={check.error} />
      {connection.status === "disabled" && (
        <p className={styles.reason}>
          {t(
            "After enabling, check the connection. Open the Authorization tab if new credentials are needed.",
          )}
        </p>
      )}
      {Object.keys(connection.safe_metadata ?? {}).length > 0 && (
        <ConfigurationSummary value={connection.safe_metadata ?? {}} />
      )}
    </>
  );
}
