import { Network } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  Page,
  ErrorNotice,
  Empty,
  Loading,
  StateBadge,
} from "../../shared/feedback";
import { Confirm, FormActions, JsonView, TextArea } from "../../shared/form";
import {
  Table,
  Pagination,
  ResourceIdentity,
  useCursor,
} from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { AuthorizationLink } from "../../shared/authorization-link";
import { MCPTools } from "./tools";
import styles from "../../shared/shared.module.css";

export function MCPPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [cleanup, setCleanup] = useState<Schema["ConnectionCleanupReceipt"]>();
  const query = useQuery({
    queryKey: ["mcp-connections", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/mcp-connections", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <Page
      title={t("MCP connections")}
      description={t(
        "Authorize remote MCP servers and make their tools available to agents.",
      )}
      actions={
        can("mcp_connection.manage") && <MCPEditor onCleanup={setCleanup} />
      }
    >
      {cleanup && (
        <div role="status">
          <h3>{t("Cleanup result")}</h3>
          <JsonView value={cleanup} />
          <Button size="sm" onClick={() => setCleanup(undefined)}>
            {t("Dismiss")}
          </Button>
        </div>
      )}
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Connection"),
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.endpoint_url}
                    icon={<Network size={17} />}
                  />
                ),
              },
              {
                label: t("Authentication"),
                render: (item) => t(`auth.${item.auth_mode}`),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <>
                    <StateBadge state={item.status} />
                    {item.status_reason && (
                      <small>{t(`state.${item.status_reason}`)}</small>
                    )}
                  </>
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <MCPEditor connectionId={item.id} onCleanup={setCleanup} />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No MCP connections")}
            description={t(
              "Connect a remote Streamable HTTP MCP endpoint to get started.",
            )}
          />
        )
      )}
    </Page>
  );
}
function MCPEditor({
  connectionId,
  onCleanup,
}: {
  connectionId?: string;
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [created, setCreated] = useState<string>(),
    [generation, setGeneration] = useState(0),
    id = connectionId ?? created;
  const query = useQuery({
    queryKey: ["mcp-connections", workspace.id, id],
    enabled: open && !!id,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: id! } },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <Dialog
      size={id ? "wide" : "default"}
      title={t(id ? "MCP connection" : "Connect MCP server")}
      description={t(
        "Endpoint and authentication mode are fixed after creation. Credentials are never returned.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setCreated(undefined);
      }}
      trigger={
        <Button
          size={connectionId ? "sm" : "md"}
          variant={connectionId ? "secondary" : "primary"}
        >
          {t(connectionId ? "Details" : "Connect MCP server")}
        </Button>
      }
    >
      {open &&
        (!id ? (
          <CreateMCP onSuccess={(value) => setCreated(value.id)} />
        ) : query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : (
          query.data &&
          (can("mcp_connection.manage") ? (
            <Tabs
              key={generation}
              label={t("MCP connection")}
              defaultValue={created ? "authorization" : "details"}
              items={[
                {
                  value: "details",
                  label: t("Details"),
                  content: (
                    <MCPSettings
                      onCleanup={onCleanup}
                      initial={query.data}
                      reload={reload}
                      close={() => setOpen(false)}
                    />
                  ),
                },
                {
                  value: "authorization",
                  label: t("Authorization"),
                  content: (
                    <MCPAuthorization initial={query.data} reload={reload} />
                  ),
                },
                {
                  value: "tools",
                  label: t("Tools"),
                  content: <MCPTools connection={query.data} />,
                },
              ]}
            />
          ) : (
            <JsonView value={query.data} />
          ))
        ))}
    </Dialog>
  );
}
function CreateMCP({
  onSuccess,
}: {
  onSuccess: (value: Schema["MCPConnection"]) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const [name, setName] = useState(""),
    [endpoint, setEndpoint] = useState(""),
    [mode, setMode] = useState<Schema["MCPAuthMode"]>("oauth"),
    [headers, setHeaders] = useState("");
  const create = useMutation({
    mutationFn: () => {
      const body = {
        name,
        endpoint_url: endpoint,
        auth_mode: mode,
        static_header_names:
          mode === "static_headers"
            ? headers
                .split("\n")
                .map((value) => value.trim())
                .filter(Boolean)
            : [],
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace_id}/mcp-connections", {
          params: {
            path: { workspace_id: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      onSuccess(result);
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <Input
        label={t("Name")}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={128}
      />
      <Input
        label={t("MCP endpoint URL")}
        type="url"
        value={endpoint}
        onChange={(event) => setEndpoint(event.target.value)}
        required
        placeholder="https://example.com/mcp"
      />
      <SelectField
        label={t("Authentication")}
        placeholder={t("Select authentication")}
        value={mode}
        onValueChange={(value) => {
          if (
            value === "none" ||
            value === "oauth" ||
            value === "bearer" ||
            value === "static_headers"
          )
            setMode(value);
        }}
        options={(["oauth", "bearer", "static_headers", "none"] as const).map(
          (value) => ({ value, label: t(`auth.${value}`) }),
        )}
      />
      {mode === "static_headers" && (
        <TextArea
          label={t("Header names")}
          hint={t(
            "One name per line. Supply secret values after creating the connection.",
          )}
          value={headers}
          onChange={setHeaders}
          required
          rows={3}
        />
      )}
      <ErrorNotice error={create.error} />
      <FormActions pending={create.isPending} label={t("Create connection")} />
    </form>
  );
}
function MCPAuthorization({
  initial,
  reload,
}: {
  initial: Schema["MCPConnection"];
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    [bearer, setBearer] = useState(""),
    [headers, setHeaders] = useState<Record<string, string>>({});
  const authorize = useMutation({
    gcTime: 0,
    mutationFn: () => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/authorize", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ authorize: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
  });
  const credentials = useMutation({
    gcTime: 0,
    mutationFn: () => {
      const body = {
        expected_version: basis.version,
        ...(basis.auth_mode === "bearer"
          ? { bearer }
          : { static_headers: headers }),
      };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/credentials", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      setBearer("");
      setHeaders({});
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      void reload();
    },
  });
  const reconnect = useMutation({
    mutationFn: () => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/reconnect", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ reconnect: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      void reload();
    },
  });
  return (
    <div className={styles.stack}>
      <StateBadge state={basis.status} />
      {basis.auth_mode === "oauth" ? (
        <>
          {authorize.data ? (
            <AuthorizationLink
              url={authorize.data.authorization_url}
              expiresAt={authorize.data.expires_at}
            />
          ) : (
            <Button
              variant="primary"
              loading={authorize.isPending}
              onClick={() => authorize.mutate()}
            >
              {t("Authorize with OAuth")}
            </Button>
          )}
          <Button onClick={() => void reload()}>
            {t("Refresh connection")}
          </Button>
        </>
      ) : (
        basis.auth_mode !== "none" && (
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              credentials.mutate();
            }}
          >
            <h3>{t("Replace credentials")}</h3>
            <p className={styles.muted}>
              {t(
                "Existing credentials are never displayed. Supply a complete replacement.",
              )}
            </p>
            {basis.auth_mode === "bearer" ? (
              <Input
                type="password"
                autoComplete="off"
                label={t("Bearer token")}
                value={bearer}
                onChange={(event) => setBearer(event.target.value)}
                required
              />
            ) : (
              basis.static_header_names.map((name) => (
                <Input
                  key={name}
                  type="password"
                  autoComplete="off"
                  label={name}
                  value={headers[name] ?? ""}
                  onChange={(event) =>
                    setHeaders((current) => ({
                      ...current,
                      [name]: event.target.value,
                    }))
                  }
                  required
                />
              ))
            )}
            <FormActions
              pending={credentials.isPending}
              label={t("Save credentials")}
            />
          </form>
        )
      )}
      <Button loading={reconnect.isPending} onClick={() => reconnect.mutate()}>
        {t("Reconnect and verify tools")}
      </Button>
      <ErrorNotice
        error={authorize.error ?? credentials.error ?? reconnect.error}
        retry={() => void reload()}
      />
    </div>
  );
}
function MCPSettings({
  initial,
  reload,
  close,
  onCleanup,
}: {
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  initial: Schema["MCPConnection"];
  reload: () => Promise<void>;
  close: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    [name, setName] = useState(initial.name);
  function done() {
    void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
    close();
  }
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: basis.id } },
          body: { name, expected_version: basis.version },
        })
        .then(data),
    onSuccess: done,
  });
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>{basis.endpoint_url}</p>
      <StateBadge state={basis.status} />
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Input
          label={t("Name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={128}
        />
        <ErrorNotice error={save.error} retry={() => void reload()} />
        <FormActions pending={save.isPending} />
      </form>
      <div className={styles.actions}>
        <Confirm
          title={t(
            basis.status === "disabled"
              ? "Enable connection"
              : "Disable connection",
          )}
          description={t(
            "This changes whether new agent calls can use the connection.",
          )}
          trigger={t(basis.status === "disabled" ? "Enable" : "Disable")}
          action={async () => {
            const action = basis.status === "disabled" ? "enable" : "disable",
              body = { expected_version: basis.version };
            await client.http.POST(
              "/api/v1/mcp-connections/{connection_id}/{action}",
              {
                params: {
                  path: { connection_id: basis.id, action },
                  header: commandHeaders(
                    workspace.id,
                    key.forBody({ action, ...body }),
                  ),
                },
                body,
              },
            );
            done();
          }}
        />
        <Confirm
          title={t("Delete MCP connection")}
          description={t(
            "This clears local credentials and removes the connection. Remote registration cleanup is best effort.",
          )}
          trigger={t("Delete")}
          danger
          action={async () => {
            const query = { expected_version: basis.version };
            const result = data(
              await client.http.DELETE(
                "/api/v1/mcp-connections/{connection_id}",
                {
                  params: {
                    path: { connection_id: basis.id },
                    query,
                    header: commandHeaders(
                      workspace.id,
                      key.forBody({ delete: basis.id, ...query }),
                    ),
                  },
                },
              ),
            );
            onCleanup(result);
            done();
          }}
        />
      </div>
    </div>
  );
}
