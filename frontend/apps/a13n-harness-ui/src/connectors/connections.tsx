import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "a13n-ui";
import { CaretDown, DotsThree, Plus, Plug } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { ConfirmAction } from "../shell/confirm-action";
import { ConnectorSetup } from "./setup";
import { AccountDialog } from "./account-dialog";
import { CreateConnector } from "./create";
import { ConnectorStatus } from "./status";
import { useConnectorCatalog, useConnectors } from "./data";
import styles from "./connectors.module.css";

export { useConnectorCatalog, useConnectors } from "./data";

export function ConnectorsSection() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const instances = useConnectors();
  const catalog = useConnectorCatalog();
  const [manage, setManage] = useState<string | null>(null);
  const [setup, setSetup] = useState<{ id: string; intent: string } | null>(
    null,
  );
  const [create, setCreate] = useState<Schema<"CatalogItem"> | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const selected = instances.data?.find((item) => item.connector_id === manage);
  async function mutate(instance: Schema<"Instance">, remove = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const params = { path: { connector_id: instance.connector_id } };
      if (remove)
        await result(
          client.DELETE("/api/connectors/{connector_id}", {
            params,
            body: { expected_version: instance.version },
          }),
        );
      else
        await result(
          client.PUT("/api/connectors/{connector_id}", {
            params,
            body: {
              kind: instance.kind,
              name: instance.name,
              enabled: !instance.enabled,
              deliver_to_bot: instance.deliver_to_bot,
              settings: instance.settings,
              rules: instance.rules,
              expected_version: instance.version,
            },
          }),
        );
      await cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      setError(failure);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <section className={styles.section} aria-label={t("Connectors")}>
      <header className={styles.header}>
        <h2>{t("Connectors")}</h2>
        <Menu>
          <MenuTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                disabled={catalog.isPending}
              />
            }
          >
            <Plus size={14} />
            {t("Add connection")}
            <CaretDown size={12} />
          </MenuTrigger>
          <MenuPopup align="end" className={styles.catalogMenu}>
            {catalog.data?.map((item) => (
              <MenuItem
                key={item.key}
                disabled={!item.available}
                onClick={() => setCreate(item)}
              >
                <Plug size={16} />
                <span>
                  <span>{item.declaration?.name ?? item.key}</span>
                  <small className={styles.menuDescription}>
                    {t(
                      item.declaration?.description ??
                        item.diagnostic ??
                        "Unavailable",
                    )}
                  </small>
                </span>
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
      </header>
      <p className={styles.description}>
        {t(
          "Connect services once for WebUI chats and Bot. Changes apply from your next message.",
        )}
      </p>
      <ErrorNotice error={error || instances.error || catalog.error} />
      {!instances.data?.length && !instances.isPending && (
        <p className={styles.empty}>
          {t("No connections yet. Add a service account to get started.")}
        </p>
      )}
      {!!instances.data?.length && (
        <SettingsSection>
          {instances.data.map((instance) => {
            const item = catalog.data?.find(
              (entry) => entry.key === instance.kind,
            );
            const available =
              item?.available &&
              instance.status.state !== "runtime_unavailable";
            return (
              <SettingsRow
                key={instance.connector_id}
                label={instance.name}
                description={item?.declaration?.name ?? instance.kind}
              >
                <div className={styles.rowActions}>
                  <ConnectorStatus instance={instance} />
                  {instance.status.state === "unconfigured" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={!available || busy}
                      onClick={() =>
                        setSetup({
                          id: instance.connector_id,
                          intent: "configure",
                        })
                      }
                    >
                      {t("Continue setup")}
                    </Button>
                  )}
                  <Menu>
                    <MenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t("Actions for {{name}}", {
                            name: instance.name,
                          })}
                        />
                      }
                    >
                      <DotsThree size={18} />
                    </MenuTrigger>
                    <MenuPopup align="end">
                      <MenuItem
                        onClick={() => setManage(instance.connector_id)}
                      >
                        {t("Manage")}
                      </MenuItem>
                      <MenuItem
                        disabled={!available || busy}
                        onClick={() =>
                          setSetup({
                            id: instance.connector_id,
                            intent: "configure",
                          })
                        }
                      >
                        {t("Set up again")}
                      </MenuItem>
                      {item?.declaration?.setup_intents?.includes("test") && (
                        <MenuItem
                          disabled={
                            !available ||
                            busy ||
                            instance.status.state !== "ready"
                          }
                          onClick={() =>
                            setSetup({
                              id: instance.connector_id,
                              intent: "test",
                            })
                          }
                        >
                          {t("Test connection")}
                        </MenuItem>
                      )}
                      <MenuItem
                        disabled={!available || busy}
                        onClick={() => {
                          void mutate(instance);
                        }}
                      >
                        {t(instance.enabled ? "Disable" : "Enable")}
                      </MenuItem>
                      <MenuSeparator />
                      <ConfirmAction
                        trigger={
                          <MenuItem
                            render={<button type="button" />}
                            nativeButton
                            closeOnClick={false}
                            disabled={busy}
                          >
                            {t("Delete connection")}
                          </MenuItem>
                        }
                        title={t("Delete connection")}
                        description={t(
                          "Delete this connection, private credentials, state and pending inputs?",
                        )}
                        confirmLabel={t("Delete connection")}
                        destructive
                        onConfirm={() => {
                          void mutate(instance, true);
                        }}
                      />
                    </MenuPopup>
                  </Menu>
                </div>
              </SettingsRow>
            );
          })}
        </SettingsSection>
      )}
      {create && (
        <CreateConnector item={create} onClose={() => setCreate(null)} />
      )}
      {setup && (
        <ConnectorSetup
          connectorId={setup.id}
          intent={setup.intent}
          name={
            instances.data?.find((item) => item.connector_id === setup.id)?.name
          }
          onClose={() => setSetup(null)}
        />
      )}
      {selected && (
        <AccountDialog
          instance={selected}
          catalog={catalog.data?.find((item) => item.key === selected.kind)}
          onClose={() => setManage(null)}
          onSetup={() => {
            setManage(null);
            setSetup({ id: selected.connector_id, intent: "configure" });
          }}
        />
      )}
    </section>
  );
}
