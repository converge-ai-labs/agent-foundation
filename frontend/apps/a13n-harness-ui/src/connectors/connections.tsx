import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Switch } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { ConnectorSetup } from "./setup";
import { SchemaForm } from "./form";
import { defaultValue, type FormSchema, type Json } from "./schema";
import styles from "./connectors.module.css";

export function useConnectors() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["connectors", "instances"],
    enabled: !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/connectors", { signal })),
    refetchInterval: 5000,
  });
}

export function useConnectorCatalog() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["connectors", "catalog"],
    enabled: !shareId,
    queryFn: ({ signal }) =>
      result(client.GET("/api/connectors/catalog", { signal })),
  });
}

export function ConnectorsSection() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const instances = useConnectors();
  const catalog = useConnectorCatalog();
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function create(item: Schema<"CatalogItem">) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const instance = await result(
        client.PUT("/api/connectors/{connector_id}", {
          params: {
            path: { connector_id: `connector-${crypto.randomUUID()}` },
          },
          body: { kind: item.key, name: item.declaration?.name ?? item.key },
        }),
      );
      await cache.invalidateQueries({ queryKey: ["connectors"] });
      setOpen(instance.connector_id);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.section} aria-label={t("Connectors")}>
      <header className={styles.header}>
        <h2>{t("Connectors")}</h2>
      </header>
      <p className={styles.description}>
        {t(
          "Connect services once for WebUI chats and Bot. Tools are captured when a new Run starts.",
        )}
      </p>
      <ErrorNotice error={error || instances.error || catalog.error} />
      <div className={styles.types}>
        {catalog.data?.map((item) => (
          <div key={item.key} className={styles.type}>
            <div>
              <strong>{item.declaration?.name ?? item.key}</strong>
              <p className={styles.description}>
                {t(
                  item.declaration?.description ??
                    item.diagnostic ??
                    "Unavailable",
                )}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !item.available}
              onClick={() => {
                void create(item);
              }}
            >
              {t("Add connection")}
            </Button>
          </div>
        ))}
      </div>
      {!instances.data?.length && !instances.isPending && (
        <p className={styles.description}>
          {t("No Connector connections yet.")}
        </p>
      )}
      {instances.data?.map((instance) => (
        <div key={instance.connector_id} className={styles.instance}>
          <div className={styles.header}>
            <div>
              <strong>{instance.name}</strong>
              <p className={styles.description}>{t(instance.status.summary)}</p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setOpen(
                  open === instance.connector_id ? null : instance.connector_id,
                )
              }
            >
              {t(open === instance.connector_id ? "Close" : "Manage")}
            </Button>
          </div>
          {open === instance.connector_id && (
            <InstanceEditor
              instance={instance}
              catalog={catalog.data?.find((item) => item.key === instance.kind)}
              onDeleted={() => setOpen(null)}
            />
          )}
        </div>
      ))}
    </section>
  );
}

function InstanceEditor({
  instance,
  catalog,
  onDeleted,
}: {
  instance: Schema<"Instance">;
  catalog?: Schema<"CatalogItem">;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const [setup, setSetup] = useState(instance.status.state === "unconfigured");
  const [baseVersion, setBaseVersion] = useState(instance.version);
  const [name, setName] = useState(instance.name);
  const [enabled, setEnabled] = useState(instance.enabled);
  const [delivery, setDelivery] = useState(instance.deliver_to_bot);
  const [settings, setSettings] = useState<Json>(
    instance.settings as Record<string, Json>,
  );
  const [rules, setRules] = useState(instance.rules ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const available =
    catalog?.available && instance.status.state !== "runtime_unavailable";
  const schema = catalog?.declaration?.configuration_schema as
    FormSchema | undefined;
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await result(
        client.PUT("/api/connectors/{connector_id}", {
          params: { path: { connector_id: instance.connector_id } },
          body: {
            kind: instance.kind,
            name,
            enabled,
            deliver_to_bot: delivery,
            settings: settings as Record<string, Json>,
            rules,
            expected_version: baseVersion,
          },
        }),
      );
      setBaseVersion(saved.version);
      await cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (
      !window.confirm(
        t(
          "Delete this connection, private credentials, state and pending inputs?",
        ),
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await result(
        client.DELETE("/api/connectors/{connector_id}", {
          params: { path: { connector_id: instance.connector_id } },
          body: { expected_version: instance.version },
        }),
      );
      await cache.invalidateQueries({ queryKey: ["connectors"] });
      onDeleted();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={styles.editor}>
      <ErrorNotice error={error} />
      {baseVersion !== instance.version && (
        <div role="status">
          <p>
            {t(
              "Saved settings changed. Your draft is preserved; reload before saving.",
            )}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setBaseVersion(instance.version);
              setName(instance.name);
              setEnabled(instance.enabled);
              setDelivery(instance.deliver_to_bot);
              setSettings(instance.settings as Record<string, Json>);
              setRules(instance.rules ?? []);
            }}
          >
            {t("Reload saved settings")}
          </Button>
        </div>
      )}
      {setup && available ? (
        <ConnectorSetup
          connectorId={instance.connector_id}
          onClose={() => setSetup(false)}
        />
      ) : (
        <Button
          variant="outline"
          disabled={!available || busy}
          onClick={() => setSetup(true)}
        >
          {t("Connection setup")}
        </Button>
      )}
      <SettingsSection title={t("Connection settings")}>
        <SettingsRow label={t("Name")}>
          <Input
            value={name}
            aria-label={t("Connection name")}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
          />
        </SettingsRow>
        <SettingsRow
          label={t("Enabled")}
          description={t(
            "Disabling holds pending input and stops new operations. It does not undo upstream work.",
          )}
        >
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            disabled={!available || busy}
            aria-label={t("Connection enabled")}
          />
        </SettingsRow>
        {catalog?.declaration?.bot_delivery && (
          <SettingsRow
            label={t("Deliver external input to Bot")}
            description={t(
              "The plugin decides what matches. Bot off, pause and human decisions still hold delivery.",
            )}
          >
            <Switch
              checked={delivery}
              onCheckedChange={setDelivery}
              disabled={!available || busy}
              aria-label={t("Deliver external input to Bot")}
            />
          </SettingsRow>
        )}
      </SettingsSection>
      {catalog?.declaration?.events?.length ? (
        <section className={styles.fields}>
          <h4>{t("Rules")}</h4>
          {rules.map((rule, index) => {
            const declaration = catalog.declaration?.events?.find(
              (event) => event.key === rule.event,
            );
            return (
              <fieldset key={rule.id} className={styles.array}>
                <div className={styles.header}>
                  <label className={styles.toggle}>
                    <Switch
                      disabled={busy || !available}
                      checked={rule.enabled ?? true}
                      onCheckedChange={(checked) =>
                        setRules(
                          rules.map((item, position) =>
                            position === index
                              ? { ...item, enabled: checked }
                              : item,
                          ),
                        )
                      }
                      aria-label={declaration?.name ?? rule.event}
                    />
                    {declaration?.name ?? rule.event}
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || !available}
                    onClick={() =>
                      setRules(
                        rules.filter((_, position) => position !== index),
                      )
                    }
                  >
                    {t("Remove")}
                  </Button>
                </div>
                {declaration?.conditions_schema && (
                  <SchemaForm
                    schema={declaration.conditions_schema as FormSchema}
                    value={rule.conditions as Record<string, Json>}
                    onChange={(value) =>
                      setRules(
                        rules.map((item, position) =>
                          position === index
                            ? {
                                ...item,
                                conditions: value as Record<string, Json>,
                              }
                            : item,
                        ),
                      )
                    }
                    disabled={busy || !available}
                  />
                )}
              </fieldset>
            );
          })}
          <div className={styles.actions}>
            {catalog.declaration.events.map((event) => (
              <Button
                key={event.key}
                variant="outline"
                size="sm"
                disabled={busy || !available}
                onClick={() =>
                  setRules([
                    ...rules,
                    {
                      id: `rule-${crypto.randomUUID()}`,
                      event: event.key,
                      enabled: true,
                      conditions: event.conditions_schema
                        ? (defaultValue(
                            event.conditions_schema as FormSchema,
                          ) as Record<string, Json>)
                        : {},
                    },
                  ])
                }
              >
                {t("Add rule: {{event}}", { event: event.name })}
              </Button>
            ))}
          </div>
        </section>
      ) : null}
      {schema && (
        <details>
          <summary>{t("Advanced configuration")}</summary>
          <SchemaForm
            schema={schema}
            value={settings}
            onChange={setSettings}
            disabled={busy || !available}
          />
        </details>
      )}
      <div className={styles.actions}>
        <Button
          disabled={busy || !available || baseVersion !== instance.version}
          onClick={() => {
            void save();
          }}
        >
          {t("Save settings")}
        </Button>
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            void remove();
          }}
        >
          {t("Delete connection")}
        </Button>
      </div>
    </div>
  );
}
