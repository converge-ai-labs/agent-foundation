import { useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useConnectors } from "./data";
import { ConnectorSetup } from "./setup";
import { ConnectorIcon } from "./presentation";
import styles from "./connectors.module.css";

export function CreateConnector({
  item,
  onClose,
}: {
  item: Schema<"CatalogItem">;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const instances = useConnectors();
  const typeName = item.declaration?.name ?? item.key;
  const [name, setName] = useState(() => {
    const names = new Set(instances.data?.map((entry) => entry.name));
    let candidate = typeName;
    for (let number = 2; names.has(candidate); number++)
      candidate = `${typeName} ${number}`;
    return candidate;
  });
  const [connectorId] = useState(() => `connector-${crypto.randomUUID()}`);
  const [created, setCreated] = useState<Schema<"Instance"> | null>(null);
  const [cleanup, setCleanup] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const id = useId();
  async function create() {
    if (pending.current || !name.trim()) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const instance = await result(
        client.PUT("/api/connectors/{connector_id}", {
          params: { path: { connector_id: connectorId } },
          body: { kind: item.key, name: name.trim() },
        }),
      ).catch(async (failure: unknown) => {
        if (
          !(failure instanceof ApiError) ||
          failure.code !== "connector_conflict"
        )
          throw failure;
        // A previous create may have committed before its response was lost.
        const saved = await result(
          client.GET("/api/connectors/{connector_id}", {
            params: { path: { connector_id: connectorId } },
          }),
        );
        if (saved.kind !== item.key) throw failure;
        return saved;
      });
      setCreated(instance);
      await cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      setError(failure);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  function closeSetup() {
    const current =
      instances.data?.find(
        (entry) => entry.connector_id === created?.connector_id,
      ) ?? created;
    if (current?.status.state === "unconfigured") setCleanup(true);
    else onClose();
  }
  async function remove() {
    const current =
      instances.data?.find(
        (entry) => entry.connector_id === created?.connector_id,
      ) ?? created;
    if (!current || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      await result(
        client.DELETE("/api/connectors/{connector_id}", {
          params: { path: { connector_id: current.connector_id } },
          body: { expected_version: current.version },
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
  if (created && !cleanup)
    return (
      <ConnectorSetup
        connectorId={created.connector_id}
        declaration={item.declaration}
        name={created.name}
        onClose={closeSetup}
      />
    );
  if (cleanup)
    return (
      <ModalFrame
        open
        title={t("Keep this connection for later?")}
        description={t(
          "Setup is incomplete. Keep the named account to continue later, or remove it.",
        )}
        closeLabel={t("Close confirmation")}
        onOpenChange={(open) => {
          if (!open && !busy) onClose();
        }}
        footer={
          <>
            <Button variant="outline" disabled={busy} onClick={onClose}>
              {t("Keep for later")}
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                void remove();
              }}
            >
              {t("Remove connection")}
            </Button>
          </>
        }
      >
        <ErrorNotice error={error} />
      </ModalFrame>
    );
  return (
    <ModalFrame
      open
      bottomStickOnMobile
      title={
        <span className={styles.identity}>
          <ConnectorIcon declaration={item.declaration} />
          <span>{t("Add {{name}}", { name: typeName })}</span>
        </span>
      }
      description={t(
        "Choose a name to distinguish this account from your other connections.",
      )}
      closeLabel={t("Close connection setup")}
      onOpenChange={(open, details) => {
        if (busy) details.cancel();
        else if (!open) onClose();
      }}
      footer={
        <>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t("Cancel")}
          </Button>
          <Button form={id} type="submit" disabled={busy || !name.trim()}>
            {t("Continue")}
          </Button>
        </>
      }
    >
      <form
        id={id}
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <ErrorNotice error={error} />
        <FormField label={t("Connection name")}>
          <Input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
            required
          />
        </FormField>
      </form>
    </ModalFrame>
  );
}
