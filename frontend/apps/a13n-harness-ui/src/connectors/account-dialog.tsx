import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame, Switch } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { GroupNameHelp } from "./group-name-help";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { ConfirmAction } from "../shell/confirm-action";
import { SchemaForm } from "./form";
import { defaultValue, type FormSchema, type Json } from "./schema";
import { ConnectorStatus } from "./status";
import { ConnectorIcon } from "./presentation";
import { useConnectors } from "./data";
import { groupNameError } from "./group-name";
import styles from "./connectors.module.css";

export function AccountDialog({
  instance,
  catalog,
  onClose,
  onSetup,
}: {
  instance: Schema<"Instance">;
  catalog?: Schema<"CatalogItem">;
  onClose: () => void;
  onSetup: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const id = useId();
  const [base, setBase] = useState(instance);
  const [name, setName] = useState(instance.name);
  const [groupName, setGroupName] = useState(instance.group_name);
  const instances = useConnectors();
  const [enabled, setEnabled] = useState(instance.enabled);
  const [delivery, setDelivery] = useState(instance.deliver_to_bot);
  const [settings, setSettings] = useState<Json>(
    instance.settings as Record<string, Json>,
  );
  const [rules, setRules] = useState(instance.rules ?? []);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const [discard, setDiscard] = useState(false);
  const dirty =
    name !== base.name ||
    groupName !== base.group_name ||
    enabled !== base.enabled ||
    delivery !== base.deliver_to_bot ||
    JSON.stringify(settings) !== JSON.stringify(base.settings) ||
    JSON.stringify(rules) !== JSON.stringify(base.rules ?? []);
  const groupError =
    groupNameError(
      groupName,
      instances.data
        ?.filter((entry) => entry.connector_id !== instance.connector_id)
        .map((entry) => entry.group_name) ?? [],
    ) ??
    (error instanceof ApiError && error.code === "connector_group_conflict"
      ? "Group name is already used by another connection."
      : undefined);
  const available =
    catalog?.available && instance.status.state !== "runtime_unavailable";
  const schema = catalog?.declaration?.configuration_schema as
    FormSchema | undefined;
  const conflict = base.version !== instance.version && dirty;
  function reset(next: Schema<"Instance">) {
    setBase(next);
    setName(next.name);
    setGroupName(next.group_name);
    setError(null);
    setEnabled(next.enabled);
    setDelivery(next.deliver_to_bot);
    setSettings(next.settings as Record<string, Json>);
    setRules(next.rules ?? []);
  }
  useEffect(() => {
    if (base.version !== instance.version && !dirty && !busy) reset(instance);
  }, [instance, base.version, dirty, busy]);
  function requestClose() {
    if (dirty) setDiscard(true);
    else onClose();
  }
  async function save() {
    if (pending.current || groupError) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = await result(
        client.PUT("/api/connectors/{connector_id}", {
          params: { path: { connector_id: instance.connector_id } },
          body: {
            kind: instance.kind,
            name,
            group_name: groupName,
            enabled,
            deliver_to_bot: delivery,
            settings: settings as Record<string, Json>,
            rules,
            expected_version: base.version,
          },
        }),
      );
      reset(saved);
      await cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      setError(failure);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function remove() {
    if (pending.current) return;
    pending.current = true;
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
      onClose();
    } catch (failure) {
      setError(failure);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <ModalFrame
        bottomStickOnMobile
        open
        keepVisibleWhenNested
        title={
          <span className={styles.identity}>
            <ConnectorIcon declaration={catalog?.declaration} />
            <span>{instance.name}</span>
          </span>
        }
        description={catalog?.declaration?.name ?? instance.kind}
        closeLabel={t("Close connection settings")}
        onOpenChange={(open, details) => {
          if (!open) {
            details.cancel();
            if (!busy) requestClose();
          }
        }}
        footer={
          <>
            <Button variant="outline" disabled={busy} onClick={requestClose}>
              {t("Close")}
            </Button>
            <Button
              type="submit"
              form={id}
              disabled={
                busy ||
                !available ||
                conflict ||
                !dirty ||
                !name.trim() ||
                !!groupError
              }
            >
              {t("Save settings")}
            </Button>
          </>
        }
      >
        <div className={styles.editor}>
          <ErrorNotice
            error={
              error instanceof ApiError &&
              error.code === "connector_group_conflict"
                ? null
                : error
            }
          />
          {conflict && (
            <div role="status">
              <p>
                {t(
                  "Saved settings changed. Your draft is preserved; reload before saving.",
                )}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => reset(instance)}
              >
                {t("Reload saved settings")}
              </Button>
            </div>
          )}
          <SettingsSection title={t("Connection")}>
            <SettingsRow
              label={t("Status")}
              description={t(instance.status.summary)}
            >
              <ConnectorStatus instance={instance} />
            </SettingsRow>
            {schema?.readOnly &&
              Object.entries(instance.settings).map(([key, value]) => (
                <SettingsRow
                  key={key}
                  label={t(schema.properties?.[key]?.title ?? key)}
                >
                  <span className={styles.summaryValue}>
                    {value === null
                      ? t("All tools")
                      : Array.isArray(value)
                        ? value.join(", ") || t("None")
                        : String(value)}
                  </span>
                </SettingsRow>
              ))}
            <SettingsRow label={t("Authentication")}>
              <span className={styles.summaryValue}>
                {instance.configured_secrets?.length
                  ? t("Saved: {{names}}", {
                      names: instance.configured_secrets.join(", "),
                    })
                  : t("No saved credentials")}
              </span>
            </SettingsRow>
            <SettingsRow label={t("Connection setup")}>
              <ConfirmAction
                trigger={
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!available || busy}
                  >
                    {t("Edit connection")}
                  </Button>
                }
                title={t("Discard unsaved changes?")}
                description={t(
                  "Your unsaved account changes will be discarded.",
                )}
                confirmLabel={t("Discard and continue")}
                confirmationRequired={dirty}
                onConfirm={onSetup}
              />
            </SettingsRow>
          </SettingsSection>
          <form
            id={id}
            className={styles.fields}
            onSubmit={(event) => {
              event.preventDefault();
              if (!conflict && dirty && available && !busy) void save();
            }}
          >
            <SettingsSection title={t("Account settings")}>
              <SettingsRow label={t("Name")}>
                <Input
                  aria-label={t("Connection name")}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  disabled={busy}
                  required
                />
              </SettingsRow>
              <SettingsRow
                label={
                  <span className="inline-flex items-center gap-1">
                    {t("Group name")}
                    <GroupNameHelp />
                  </span>
                }
                stackOnNarrow
              >
                <FormField
                  label={t("Group name")}
                  hideLabel
                  description={
                    groupName !== base.group_name
                      ? t(
                          "Changing this group requires a new Run to use its tools.",
                        )
                      : undefined
                  }
                  error={groupError ? t(groupError) : undefined}
                >
                  <Input
                    value={groupName}
                    onChange={(event) => {
                      setGroupName(event.target.value);
                      setError(null);
                    }}
                    disabled={busy}
                    autoCapitalize="none"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={32}
                    required
                  />
                </FormField>
              </SettingsRow>
              <SettingsRow
                label={t("Enabled")}
                description={t(
                  "Disabling holds pending input and stops new operations. It does not undo upstream work.",
                )}
              >
                <Switch
                  aria-label={t("Connection enabled")}
                  checked={enabled}
                  onCheckedChange={setEnabled}
                  disabled={!available || busy}
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
                    aria-label={t("Deliver external input to Bot")}
                    checked={delivery}
                    onCheckedChange={setDelivery}
                    disabled={!available || busy}
                  />
                </SettingsRow>
              )}
            </SettingsSection>
            {!!catalog?.declaration?.events?.length && (
              <section className={styles.fields}>
                <h3>{t("Rules")}</h3>
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
                          type="button"
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
                      type="button"
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
            )}
            {schema && !schema.readOnly && (
              <SettingsSection title={t("Configuration")}>
                <SchemaForm
                  schema={schema}
                  value={settings}
                  onChange={setSettings}
                  disabled={busy || !available}
                />
              </SettingsSection>
            )}
          </form>
          <SettingsSection title={t("Remove connection")}>
            <SettingsRow
              label={t("Delete connection")}
              description={t(
                "Delete this connection, private credentials, state and pending inputs?",
              )}
            >
              <ConfirmAction
                trigger={
                  <Button variant="ghost" size="sm" disabled={busy}>
                    {t("Delete")}
                  </Button>
                }
                title={t("Delete connection")}
                description={t(
                  "Delete this connection, private credentials, state and pending inputs?",
                )}
                confirmLabel={t("Delete connection")}
                destructive
                onConfirm={() => {
                  void remove();
                }}
              />
            </SettingsRow>
          </SettingsSection>
        </div>
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={discard}
        title={t("Discard unsaved changes?")}
        description={t("Your unsaved account changes will be discarded.")}
        closeLabel={t("Close confirmation")}
        onOpenChange={setDiscard}
        footer={
          <>
            <Button variant="outline" onClick={() => setDiscard(false)}>
              {t("Keep editing")}
            </Button>
            <Button onClick={onClose}>{t("Discard changes")}</Button>
          </>
        }
      />
    </>
  );
}
