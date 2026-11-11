import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useConnectorCatalog, useConnectors } from "./connections";
import { ConnectorSetup } from "./setup";
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
  const { client, shareId } = useTransport();
  const catalog = useConnectorCatalog();
  const instances = useConnectors();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<string | null>(
    offer.connector_id ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const declaration = catalog.data?.find(
    (item) => item.key === offer.connector_key,
  );
  const name = declaration?.declaration?.name ?? offer.connector_key;
  const eligible =
    instances.data?.filter((item) => item.kind === offer.connector_key) ?? [];
  async function create() {
    setBusy(true);
    setError(null);
    try {
      const instance = await result(
        client.PUT("/api/connectors/{connector_id}", {
          params: {
            path: { connector_id: `connector-${crypto.randomUUID()}` },
          },
          body: { kind: offer.connector_key, name },
        }),
      );
      await cache.invalidateQueries({ queryKey: ["connectors"] });
      setTarget(instance.connector_id);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.offer} aria-label={t("Connector setup offer")}>
      <div className={styles.header}>
        <strong>{t("Connect {{name}}", { name })}</strong>
        {!shareId && (
          <Button variant="outline" size="sm" onClick={() => setOpen(!open)}>
            {t(open ? "Close" : "Set up connection")}
          </Button>
        )}
      </div>
      {offer.text && <p className={styles.description}>{offer.text}</p>}
      {shareId ? (
        <p className={styles.description}>
          {t("Only the instance owner can configure this connection.")}
        </p>
      ) : (
        open && (
          <>
            <ErrorNotice error={error} />
            {target ? (
              <ConnectorSetup
                connectorId={target}
                onClose={() => {
                  setOpen(false);
                }}
              />
            ) : (
              <div className={styles.fields}>
                {eligible.map((instance) => (
                  <Button
                    key={instance.connector_id}
                    variant="outline"
                    onClick={() => setTarget(instance.connector_id)}
                  >
                    {instance.name}
                  </Button>
                ))}
                <Button
                  variant="outline"
                  disabled={busy || !declaration?.available}
                  onClick={() => {
                    void create();
                  }}
                >
                  {t("New connection")}
                </Button>
              </div>
            )}
          </>
        )
      )}
    </section>
  );
}
