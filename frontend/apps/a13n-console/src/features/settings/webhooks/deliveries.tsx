import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data } from "../../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../../shared/collection";
import { Confirm } from "../../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { JsonView } from "../../../shared/forms";
import { CopyableId } from "../../../shared/identity";
import shared from "../../../shared/shared.module.css";
import { webhookKeys, webhookQueries, type Delivery } from "./api";
import { canRedeliver } from "./draft";
import styles from "./webhooks.module.css";

const deliveryLabels = {
  pending: "Pending delivery",
  delivered: "Delivered",
  dead: "Retries stopped",
};

function DeliveryStatus({ delivery }: { delivery: Delivery }) {
  const { t } = useTranslation();
  return (
    <StatePill
      state={delivery.status}
      label={t(deliveryLabels[delivery.status])}
    />
  );
}

export function WebhookDeliveries({
  subscriptionId,
}: {
  subscriptionId: string;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const cache = useQueryClient(),
    page = useCursor();
  const [selectedId, setSelectedId] = useState<string>();
  const [queuedId, setQueuedId] = useState<string>();
  const options = webhookQueries(client, workspace.id).deliveries(
    subscriptionId,
    page.cursor,
  );
  const query = useQuery({ ...options, enabled: can("admin") });
  const selected = query.data?.items.find((item) => item.id === selectedId);
  const refresh = () =>
    cache.invalidateQueries({
      queryKey: webhookKeys(workspace.id).deliveries(subscriptionId),
    });
  if (!can("admin")) return null;
  return (
    <div className={shared.stack}>
      <div className={styles.toolbar}>
        <p className={styles.note}>
          {t(
            "Each delivery keeps the URL, payload and signing secret saved when it was queued.",
          )}
        </p>
        <Button
          variant="outline"
          size="sm"
          loading={query.isFetching}
          onClick={() => void refresh()}
        >
          <ArrowClockwiseIcon size={14} aria-hidden="true" />
          {t("Refresh")}
        </Button>
      </div>
      {queuedId && (
        <p role="status" className={styles.note}>
          {t("Delivery queued again. Refresh to check its result.")}
        </p>
      )}
      {query.error && (
        <ErrorNotice error={query.error} retry={() => void refresh()} />
      )}
      {query.isPending ? (
        <Loading variant="table" columns={4} rows={4} />
      ) : (
        query.data &&
        (query.data.items.length ? (
          <>
            <ResourceTable
              items={query.data.items}
              caption={t("Deliveries")}
              onRowActivate={(item) => setSelectedId(item.id)}
              columns={[
                {
                  label: t("Event"),
                  tone: "primary",
                  render: (item) => (
                    <div className={styles.stacked}>
                      <button
                        type="button"
                        className={styles.link}
                        onClick={() => setSelectedId(item.id)}
                      >
                        <code className={styles.eventName}>
                          {String(item.payload.type ?? t("Unknown event"))}
                        </code>
                      </button>
                      <span className={styles.note}>{item.id}</span>
                    </div>
                  ),
                },
                {
                  label: t("Status"),
                  render: (item) => <DeliveryStatus delivery={item} />,
                },
                { label: t("Attempts"), render: (item) => item.attempts },
                {
                  label: t("Created"),
                  render: (item) => (
                    <Timestamp value={item.created_at} relative />
                  ),
                },
              ]}
            />
            <CollectionFooter
              count={t("{{count}} deliveries", {
                count: query.data.items.length,
              })}
            >
              <Pagination page={page} next={query.data.next_cursor} />
            </CollectionFooter>
          </>
        ) : (
          <Empty
            title={t("No deliveries yet")}
            description={t(
              "Matching lifecycle events appear here after they are queued.",
            )}
          />
        ))
      )}
      <ModalFrame
        size="lg"
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelectedId(undefined);
        }}
        title={t("Delivery details")}
        description={t(
          "Inspect the saved notification and its latest delivery result.",
        )}
        closeLabel={t("Close")}
      >
        {selected && (
          <div className={shared.stack}>
            <dl className={styles.facts}>
              <dt>{t("Delivery ID")}</dt>
              <dd>
                <CopyableId value={selected.id} />
              </dd>
              <dt>{t("Status")}</dt>
              <dd>
                <DeliveryStatus delivery={selected} />
              </dd>
              <dt>{t("Saved endpoint")}</dt>
              <dd>{selected.url}</dd>
              <dt>{t("Attempts")}</dt>
              <dd>{selected.attempts}</dd>
              <dt>{t("Created")}</dt>
              <dd>
                <Timestamp value={selected.created_at} />
              </dd>
              {selected.status === "pending" && (
                <>
                  <dt>{t("Next eligible attempt")}</dt>
                  <dd>
                    <Timestamp value={selected.available_at} />
                  </dd>
                </>
              )}
              {selected.delivered_at && (
                <>
                  <dt>{t("Delivered at")}</dt>
                  <dd>
                    <Timestamp value={selected.delivered_at} />
                  </dd>
                </>
              )}
              {selected.last_error && (
                <>
                  <dt>{t("Last error")}</dt>
                  <dd>
                    <code>{selected.last_error}</code>
                  </dd>
                </>
              )}
            </dl>
            {selected.status === "dead" &&
              selected.last_error === "signing_secret_unavailable" && (
                <p className={styles.note}>
                  {t(
                    "This delivery cannot be retried because its saved signing secret is unavailable. A replacement secret applies only to future deliveries.",
                  )}
                </p>
              )}
            <DisclosureSection title={t("Event payload")}>
              <JsonView value={selected.payload} />
            </DisclosureSection>
            {canRedeliver(selected) && (
              <Confirm
                title={t("Retry delivery")}
                trigger={t("Retry delivery")}
                subject={selected.id}
                description={t(
                  "Queue this delivery again with its original ID, URL, payload and signing secret. The receiving application must handle duplicates.",
                )}
                retry={() => void refresh()}
                action={async () => {
                  try {
                    const saved = data(
                      await client
                        .workspace(workspace.id)
                        .POST(
                          "/api/v1/subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver",
                          {
                            params: {
                              path: {
                                subscription_id: subscriptionId,
                                delivery_id: selected.id,
                              },
                            },
                          },
                        ),
                    );
                    cache.setQueryData(
                      options.queryKey,
                      (current) =>
                        current && {
                          ...current,
                          items: current.items.map((item) =>
                            item.id === saved.id ? saved : item,
                          ),
                        },
                    );
                    setQueuedId(saved.id);
                  } finally {
                    void refresh();
                  }
                }}
                onSuccess={() => {}}
              />
            )}
          </div>
        )}
      </ModalFrame>
    </div>
  );
}
