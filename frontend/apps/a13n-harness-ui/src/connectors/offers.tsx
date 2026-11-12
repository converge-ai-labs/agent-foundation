import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Button } from "a13n-ui";
import { Check } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { useConnectorCatalog, useConnectors } from "./data";
import { ConnectorSetup } from "./setup";
import { CreateConnector } from "./create";
import { ConnectorStatus } from "./status";
import { ConnectorIcon } from "./presentation";
import styles from "./connectors.module.css";

export function useConnectorOffers(threadId: string, enabled = true) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["connectors", "offers", threadId],
    enabled: enabled && !shareId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/connector-setup-offers", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
    refetchInterval: 5000,
  });
}

export function ConnectorOffer({ offer }: { offer: Schema<"SetupOffer"> }) {
  const { t } = useTranslation();
  const { shareId } = useTransport();
  const catalog = useConnectorCatalog();
  const instances = useConnectors();
  const [setup, setSetup] = useState<string | null>(null);
  const [create, setCreate] = useState(false);
  const [target, setTarget] = useState<string | null>(
    offer.connector_id ?? null,
  );
  const group = useId();
  const declaration = catalog.data?.find(
    (item) => item.key === offer.connector_key,
  );
  const name = declaration?.declaration?.name ?? offer.connector_key;
  const eligible =
    instances.data?.filter(
      (item) =>
        item.kind === offer.connector_key &&
        (!offer.connector_id || item.connector_id === offer.connector_id),
    ) ?? [];
  const ready = eligible.find((item) => item.status.state === "ready");
  const selected =
    eligible.find((item) => item.connector_id === target) ?? eligible[0];
  return (
    <section className={styles.offer} aria-label={t("Connector setup offer")}>
      <div className={styles.header}>
        <strong>
          <ConnectorIcon declaration={declaration?.declaration} />
          {ready ? (
            <>
              <Check size={16} aria-hidden="true" />{" "}
              {t("{{name}} is connected", { name: ready.name })}
            </>
          ) : (
            t("Connect {{name}}", { name })
          )}
        </strong>
        {!shareId && ready && (
          <Button
            variant="ghost"
            size="sm"
            render={<Link to="/settings/connections" />}
          >
            {t("Manage")}
          </Button>
        )}
      </div>
      {offer.text && <p className={styles.description}>{offer.text}</p>}
      {shareId ? (
        <p className={styles.description}>
          {t("Only the instance owner can configure this connection.")}
        </p>
      ) : ready ? (
        <p className={styles.description}>
          {t("Send a new message to use it. No operation was replayed.")}
        </p>
      ) : (
        <>
          {!!eligible.length && (
            <fieldset className={styles.accountPicker}>
              <legend className={styles.description}>
                {t("Choose an account")}
              </legend>
              {eligible.map((instance) => (
                <label
                  key={instance.connector_id}
                  className={styles.accountChoice}
                >
                  <input
                    type="radio"
                    name={group}
                    value={instance.connector_id}
                    checked={selected?.connector_id === instance.connector_id}
                    onChange={() => setTarget(instance.connector_id)}
                  />
                  <span>{instance.name}</span>
                  <ConnectorStatus instance={instance} />
                </label>
              ))}
            </fieldset>
          )}
          <div className={styles.actions}>
            {selected && (
              <Button
                variant="outline"
                size="sm"
                disabled={
                  !declaration?.available ||
                  selected.status.state === "runtime_unavailable"
                }
                onClick={() => setSetup(selected.connector_id)}
              >
                {t(
                  selected.status.state === "unconfigured"
                    ? "Continue setup"
                    : "Use this account",
                )}
              </Button>
            )}
            {declaration && (
              <Button
                variant={selected ? "ghost" : "outline"}
                size="sm"
                disabled={!declaration.available}
                onClick={() => setCreate(true)}
              >
                {selected
                  ? t("Add another account")
                  : t("Connect {{name}}", { name })}
              </Button>
            )}
          </div>
        </>
      )}
      {!shareId && setup && (
        <ConnectorSetup
          connectorId={setup}
          declaration={declaration?.declaration}
          name={eligible.find((item) => item.connector_id === setup)?.name}
          onClose={() => setSetup(null)}
        />
      )}
      {!shareId && create && declaration && (
        <CreateConnector item={declaration} onClose={() => setCreate(false)} />
      )}
    </section>
  );
}
