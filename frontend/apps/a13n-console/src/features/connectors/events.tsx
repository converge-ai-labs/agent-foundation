import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  SettingsSection,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import {
  FormActions,
  JsonView,
  SchemaFields,
  jsonObject,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import { Pagination, useCursor } from "../../shared/collection";
import styles from "./events.module.css";

type Trigger = Schema["ConnectorTrigger"];

export function ConnectorEvents({
  connection,
}: {
  connection: Schema["Connection"];
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    cache = useQueryClient();
  const api = client.workspace(workspace.id);
  const providerId = connection.connector_provider_id!;
  const app = "app" in connection.config ? connection.config.app : "";
  const [selected, setSelected] = useState<Trigger>();
  const [creating, setCreating] = useState(false);
  const page = useCursor({ connectionId: connection.id });
  const key = ["connector-events", workspace.id, connection.id];
  const webhook = useQuery({
    queryKey: [...key, "webhook"],
    queryFn: ({ signal }) =>
      api
        .GET("/api/v1/connector-providers/{provider_id}/event-webhook", {
          params: { path: { provider_id: providerId } },
          signal,
        })
        .then(data),
    refetchInterval: (query) => (query.state.data?.operation ? 3000 : false),
  });
  const triggers = useQuery({
    queryKey: [...key, "triggers", page.cursor],
    queryFn: ({ signal }) =>
      api
        .GET("/api/v1/connector-triggers", {
          params: {
            query: {
              connection_id: connection.id,
              cursor: page.cursor,
              limit: 20,
            },
          },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.operation_kind)
        ? 3000
        : false,
  });
  const configure = useMutation({
    mutationFn: async (rotate: boolean) => {
      if (!webhook.data) return;
      return data(
        await api.POST(
          "/api/v1/connector-providers/{provider_id}/event-webhook",
          {
            params: {
              path: { provider_id: providerId },
              query: { rotate },
              header: ifMatch(
                rowTag({ id: providerId, version: webhook.data.version }),
              ),
            },
          },
        ),
      );
    },
    onSettled: () => cache.invalidateQueries({ queryKey: key }),
  });
  const transition = useMutation({
    mutationFn: async ({
      trigger,
      command,
    }: {
      trigger: Trigger;
      command: "enable" | "pause" | "retire" | "reconcile";
    }) =>
      data(
        await api.POST("/api/v1/connector-triggers/{trigger_id}/{command}", {
          params: {
            path: { trigger_id: trigger.id, command },
            header: ifMatch(rowTag(trigger)),
          },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: key }),
  });
  return (
    <div className={styles.stack}>
      <SettingsSection
        title={t("Event delivery")}
        description={t(
          "Receive events from this account and optionally start an agent.",
        )}
      >
        {webhook.isPending ? (
          <Loading variant="form" rows={1} />
        ) : webhook.error ? (
          <ErrorNotice error={webhook.error} />
        ) : (
          <>
            <p>
              {webhook.data?.configured
                ? t("The provider callback is configured.")
                : t(
                    "Configure the provider callback before creating subscriptions.",
                  )}
            </p>
            {webhook.data?.url ? (
              <code className={styles.url}>{webhook.data.url}</code>
            ) : (
              <p>{t("Ask an operator to configure the public event URL.")}</p>
            )}
            {webhook.data?.operation && (
              <p role="status">{t("Updating callback…")}</p>
            )}
            {webhook.data?.failure && (
              <p role="alert">{webhook.data.failure}</p>
            )}
            {can("admin") && (
              <Button
                variant="outline"
                size="sm"
                disabled={
                  !webhook.data?.url ||
                  (!!webhook.data.operation && !webhook.data.retryable) ||
                  configure.isPending
                }
                onClick={() =>
                  configure.mutate(
                    !!webhook.data?.configured && !webhook.data?.retryable,
                  )
                }
              >
                {webhook.data?.retryable
                  ? t("Retry callback setup")
                  : webhook.data?.configured
                    ? t("Rotate signing key")
                    : t("Configure callback")}
              </Button>
            )}
            {configure.error && <ErrorNotice error={configure.error} />}
          </>
        )}
      </SettingsSection>
      <div className={styles.heading}>
        <h3>{t("Event subscriptions")}</h3>
        {can("write") && !creating && (
          <Button
            size="sm"
            disabled={
              !webhook.data?.configured || connection.status !== "ready"
            }
            onClick={() => setCreating(true)}
          >
            {t("Add subscription")}
          </Button>
        )}
      </div>
      {creating && (
        <TriggerForm
          connection={connection}
          app={app}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            void cache.invalidateQueries({ queryKey: key });
          }}
        />
      )}
      {triggers.error && <ErrorNotice error={triggers.error} />}
      {triggers.isPending ? (
        <Loading variant="form" rows={2} />
      ) : (
        <div className={styles.list}>
          {triggers.data?.items.length === 0 && (
            <p>{t("No event subscriptions yet.")}</p>
          )}
          {triggers.data?.items.map((trigger) => (
            <div key={trigger.id} className={styles.subscription}>
              <button
                className={styles.identity}
                onClick={() => setSelected(trigger)}
              >
                <strong>{trigger.name}</strong>
                <span>{trigger.event_type}</span>
              </button>
              <span>
                {trigger.operation_kind && !trigger.failure
                  ? t("Updating subscription…")
                  : t(trigger.state)}
              </span>
              {trigger.failure && <p role="alert">{trigger.failure}</p>}
              {can("write") && trigger.retryable && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={transition.isPending}
                  onClick={() =>
                    transition.mutate({ trigger, command: "reconcile" })
                  }
                >
                  {t("Retry subscription setup")}
                </Button>
              )}
              {can("write") && trigger.state !== "retired" && (
                <div className={styles.actions}>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={
                      (!!trigger.operation_kind &&
                        trigger.state !== "enabled") ||
                      transition.isPending
                    }
                    onClick={() =>
                      transition.mutate({
                        trigger,
                        command:
                          trigger.state === "enabled" ? "pause" : "enable",
                      })
                    }
                  >
                    {trigger.state === "enabled" ? t("Pause") : t("Enable")}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={transition.isPending}
                    onClick={() =>
                      transition.mutate({ trigger, command: "retire" })
                    }
                  >
                    {t("Retire")}
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {transition.error && <ErrorNotice error={transition.error} />}
      <Pagination page={page} next={triggers.data?.next_cursor} />
      <p className={styles.note}>
        {t(
          "Pausing or retiring a subscription affects new events. Events already accepted continue to run.",
        )}
      </p>
      {selected && (
        <TriggerDetails
          key={selected.id}
          trigger={
            triggers.data?.items.find((item) => item.id === selected.id) ??
            selected
          }
          onChanged={() => cache.invalidateQueries({ queryKey: key })}
        />
      )}
    </div>
  );
}

function TriggerForm({
  connection,
  app,
  onSaved,
  onClose,
  trigger,
}: {
  connection: Schema["Connection"];
  app: string;
  onSaved: () => void;
  onClose: () => void;
  trigger?: Trigger;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const api = client.workspace(workspace.id),
    request = useIdempotency();
  const [name, setName] = useState(trigger?.name ?? ""),
    [eventType, setType] = useState(trigger?.event_type ?? "");
  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  const [mode, setMode] = useState(trigger?.target.mode ?? "store_only");
  const target =
    trigger?.target.mode === "new_thread" ? trigger.target : undefined;
  const [agentId, setAgent] = useState(target?.agent_id ?? ""),
    [principalId, setPrincipal] = useState(target?.principal_id ?? "");
  const [instruction, setInstruction] = useState(target?.instruction ?? "");
  const definitions = useQuery({
    queryKey: [
      "connector-event-types",
      workspace.id,
      connection.connector_provider_id,
      app,
    ],
    enabled: !trigger && can("run"),
    queryFn: ({ signal }) =>
      api
        .GET(
          "/api/v1/connector-providers/{provider_id}/apps/{app}/event-types",
          {
            params: {
              path: { provider_id: connection.connector_provider_id!, app },
            },
            signal,
          },
        )
        .then(data),
  });
  const agents = useQuery({
    queryKey: ["connector-event-agents", workspace.id],
    enabled: mode === "new_thread" && can("admin"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        api
          .GET("/api/v1/agents", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const accounts = useQuery({
    queryKey: ["connector-event-accounts", workspace.id],
    enabled: mode === "new_thread" && can("admin"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        api
          .GET("/api/v1/workspaces/{workspace_id}/service-accounts", {
            params: {
              path: { workspace_id: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const definition = definitions.data?.items.find(
    (item) => item.key === eventType,
  );
  const save = useMutation({
    mutationFn: async () => {
      const selectedTarget: Schema["TriggerCreate"]["target"] =
        mode === "new_thread"
          ? {
              mode: "new_thread",
              agent_id: agentId,
              principal_id: principalId,
              ...(target?.agent_id === agentId
                ? { agent_revision_id: target.agent_revision_id }
                : {}),
              ...(instruction ? { instruction } : {}),
            }
          : { mode: "store_only" };
      if (trigger)
        return data(
          await api.PATCH("/api/v1/connector-triggers/{trigger_id}", {
            params: {
              path: { trigger_id: trigger.id },
              header: ifMatch(rowTag(trigger)),
            },
            body: { name, target: selectedTarget },
          }),
        );
      if (!definition) throw new Error(t("Select an event type."));
      const configured = withSchemaValues(definition.config_schema, parameters);
      validateSettings(definition.config_schema, configured);
      const body = {
        name: name || definition.name,
        connection_id: connection.id,
        event_type: definition.key,
        parameters: jsonObject(JSON.stringify(configured)),
        target: selectedTarget,
      };
      return data(
        await api.POST("/api/v1/connector-triggers", {
          params: { header: { "Idempotency-Key": request.forBody(body) } },
          body,
        }),
      );
    },
    onSuccess: onSaved,
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField label={t("Name")}>
        <Input value={name} onChange={(event) => setName(event.target.value)} />
      </FormField>
      {!trigger && (
        <>
          {definitions.error && <ErrorNotice error={definitions.error} />}
          <ChoiceField
            label={t("Event type")}
            value={eventType}
            onValueChange={(value) => {
              setType(value);
              setParameters({});
            }}
            options={(definitions.data?.items ?? []).map((item) => ({
              value: item.key,
              label: item.name,
            }))}
          />
          {definition && (
            <>
              <p>{definition.description}</p>
              {definition.instructions && <p>{definition.instructions}</p>}
              {definition.requires_provider_setup && (
                <p>
                  {t(
                    "Complete the event setup in the provider before enabling this subscription.",
                  )}
                </p>
              )}
              <SchemaFields
                schema={definition.config_schema}
                value={parameters}
                onChange={setParameters}
              />
            </>
          )}
        </>
      )}
      <ChoiceField
        label={t("When an event arrives")}
        value={mode}
        onValueChange={(value) => setMode(value as "store_only" | "new_thread")}
        options={[
          { value: "store_only", label: t("Save event") },
          ...(can("admin")
            ? [{ value: "new_thread", label: t("Start an agent") }]
            : []),
        ]}
      />
      {mode === "new_thread" && (
        <>
          {agents.error && <ErrorNotice error={agents.error} />}
          {accounts.error && <ErrorNotice error={accounts.error} />}
          <ChoiceField
            label={t("Agent")}
            value={agentId}
            onValueChange={setAgent}
            options={(agents.data ?? []).map((agent) => ({
              value: agent.id,
              label: agent.name,
            }))}
          />
          <ChoiceField
            label={t("Service account")}
            value={principalId}
            onValueChange={setPrincipal}
            options={(accounts.data ?? [])
              .filter((account) => account.status === "active")
              .map((account) => ({ value: account.id, label: account.name }))}
          />
          <FormField label={t("Instructions")}>
            <Input
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
            />
          </FormField>
          <p className={styles.note}>
            {t(
              "The agent revision is fixed when you save. Each event starts a new conversation.",
            )}
          </p>
        </>
      )}
      {save.error && <ErrorNotice error={save.error} />}
      <FormActions
        pending={save.isPending}
        onCancel={onClose}
        label={t("Save")}
        disabled={
          (!trigger && !definition) ||
          (mode === "new_thread" && (!agentId || !principalId))
        }
      />
    </form>
  );
}

function TriggerDetails({
  trigger,
  onChanged,
}: {
  trigger: Trigger;
  onChanged: () => Promise<unknown>;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const api = client.workspace(workspace.id),
    page = useCursor({ triggerId: trigger.id });
  const [editing, setEditing] = useState(false);
  const connection = useQuery({
    queryKey: ["connections", workspace.id, trigger.connection_id],
    enabled: editing,
    queryFn: () =>
      api
        .GET("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: trigger.connection_id } },
        })
        .then(data),
  });
  const events = useQuery({
    queryKey: [
      "connector-event-history",
      workspace.id,
      trigger.id,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      api
        .GET("/api/v1/connector-events", {
          params: {
            query: { trigger_id: trigger.id, limit: 20, cursor: page.cursor },
          },
          signal,
        })
        .then(data),
    refetchInterval: 5000,
  });
  const retry = useMutation({
    mutationFn: (event: Schema["ConnectorEvent"]) =>
      api
        .POST("/api/v1/connector-events/{event_id}/retry", {
          params: {
            path: { event_id: event.id },
            header: ifMatch(rowTag(event)),
          },
        })
        .then(data),
    onSuccess: () => events.refetch(),
  });
  return (
    <section className={styles.stack}>
      <div className={styles.heading}>
        <h3>{trigger.name}</h3>
        {can("write") && trigger.state !== "retired" && (
          <Button
            variant="outline"
            size="sm"
            disabled={!!trigger.operation_kind}
            onClick={() => setEditing(true)}
          >
            {t("Edit")}
          </Button>
        )}
      </div>
      {editing && connection.data && (
        <TriggerForm
          key={trigger.id}
          trigger={trigger}
          connection={connection.data}
          app={trigger.definition.app}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void onChanged();
          }}
        />
      )}
      {connection.error && <ErrorNotice error={connection.error} />}
      <h3>{t("Recent events")}</h3>
      {events.error && <ErrorNotice error={events.error} />}
      {events.data?.items.length === 0 && <p>{t("No events received yet.")}</p>}
      {events.data?.items.map((event) => (
        <EventDetails key={event.id} event={event}>
          {event.failure && <p role="alert">{event.failure}</p>}
          {event.payload ? (
            <JsonView value={event.payload} />
          ) : (
            <p>{t("The event payload has expired.")}</p>
          )}
          {can("admin") && event.retryable && (
            <Button
              variant="outline"
              size="sm"
              disabled={retry.isPending}
              onClick={() => retry.mutate(event)}
            >
              {t("Retry delivery")}
            </Button>
          )}
        </EventDetails>
      ))}
      {retry.error && <ErrorNotice error={retry.error} />}
      <Pagination page={page} next={events.data?.next_cursor} />
    </section>
  );
}

function EventDetails({
  event,
  children,
}: {
  event: Schema["ConnectorEvent"];
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const thread = useQuery({
    queryKey: ["connector-event-thread", workspace.id, event.thread_id],
    enabled: open && !!event.thread_id,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id: event.thread_id! } },
          signal,
        })
        .then(data),
    staleTime: Infinity,
  });
  return (
    <details
      className={styles.event}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <Timestamp value={event.occurred_at} /> · {t(event.state)}
      </summary>
      {thread.data && (
        <Link
          to={`${basePath}/sessions/${thread.data.session_id}/threads/${thread.data.id}`}
        >
          {t("Open conversation")}
        </Link>
      )}
      {thread.error && <ErrorNotice error={thread.error} />}
      {children}
    </details>
  );
}
