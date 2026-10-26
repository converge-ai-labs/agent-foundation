import { WebhooksLogoIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, SettingsRow, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, rowTag } from "../../../shared/api";
import { Empty } from "../../../shared/collection";
import { Confirm } from "../../../shared/dialogs";
import { ErrorNotice, Loading, StatePill } from "../../../shared/feedback";
import { CopyableId, IconTile } from "../../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../../shared/page";
import shared from "../../../shared/shared.module.css";
import { webhookKeys, webhookQueries, type Subscription } from "./api";
import { WebhookDeliveries } from "./deliveries";
import { WebhookEditor } from "./editor";
import styles from "./webhooks.module.css";

export function WebhookDetail({ subscriptionId }: { subscriptionId: string }) {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [tab, setTab] = useTabParam(["deliveries", "configuration"]);
  const query = useQuery({
    ...webhookQueries(client, workspace.id).subscription(subscriptionId),
    enabled: can("admin"),
  });
  if (!can("admin"))
    return (
      <Empty
        title={t("Access unavailable")}
        description={t("Workspace administrator access is required.")}
      />
    );
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const subscription = query.data;
  return (
    <DetailPage
      back={`${basePath}/settings/webhooks`}
      backLabel={t("Webhooks")}
      header={
        <DetailHeader
          name={subscription.name}
          description={subscription.url}
          avatar={
            <IconTile size={36}>
              <WebhooksLogoIcon size={18} aria-hidden="true" />
            </IconTile>
          }
          status={
            <StatePill state={subscription.enabled ? "enabled" : "disabled"} />
          }
          actions={<WebhookEditor subscription={subscription} />}
        />
      }
      tabs={[
        { value: "deliveries", label: t("Deliveries") },
        { value: "configuration", label: t("Configuration") },
      ]}
      tab={tab}
      onTabChange={setTab}
    >
      {query.error && (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      )}
      {tab === "deliveries" ? (
        <WebhookDeliveries
          key={subscription.id}
          subscriptionId={subscription.id}
        />
      ) : (
        <WebhookConfiguration
          subscription={subscription}
          reload={() => void query.refetch()}
        />
      )}
    </DetailPage>
  );
}

function WebhookConfiguration({
  subscription,
  reload,
}: {
  subscription: Subscription;
  reload: () => void;
}) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const cache = useQueryClient(),
    navigate = useNavigate(),
    keys = webhookKeys(workspace.id);
  const toggle = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/subscriptions/{subscription_id}", {
          params: { path: { subscription_id: subscription.id } },
          headers: ifMatch(rowTag(subscription)),
          body: { enabled: !subscription.enabled },
        })
        .then(data),
    onSuccess: (saved) => {
      cache.setQueryData(keys.subscription(saved.id), saved);
      void cache.invalidateQueries({ queryKey: keys.list });
    },
  });
  return (
    <div className={shared.stack}>
      <SettingsSection variant="outlined" title={t("Subscription scope")}>
        <SettingsRow label={t("Events")}>
          <div className={styles.events}>
            {subscription.kinds.map((kind) => (
              <code key={kind}>{kind}</code>
            ))}
          </div>
        </SettingsRow>
        <SettingsRow
          label={t("Matching conditions")}
          description={t("All specified conditions must match.")}
        >
          <div className={styles.stacked}>
            {Object.values(subscription.filter).some(Boolean)
              ? Object.entries(subscription.filter)
                  .filter(([, value]) => value)
                  .map(([key, value]) => (
                    <span key={key}>
                      {key}: <CopyableId value={value!} />
                    </span>
                  ))
              : t("Entire workspace")}
          </div>
        </SettingsRow>
        <SettingsRow label={t("Subscription ID")}>
          <CopyableId value={subscription.id} />
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="outlined" title={t("Signature verification")}>
        <SettingsRow
          label={t("Signing secret configured")}
          description={t(
            "The secret is shown only at creation. Edit this Webhook to set a replacement.",
          )}
        >
          <span className={styles.note}>HMAC-SHA256</span>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="outlined" title={t("Manage")}>
        <SettingsRow
          label={t(
            subscription.enabled
              ? "Disable subscription"
              : "Enable subscription",
          )}
          description={t(
            "Disabling stops new notifications. Queued deliveries continue.",
          )}
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            loading={toggle.isPending}
            onClick={() => toggle.mutate()}
          >
            {t(subscription.enabled ? "Disable" : "Enable")}
          </Button>
        </SettingsRow>
        <SettingsRow
          label={t("Delete Webhook")}
          description={t(
            "Stops new notifications. Queued deliveries still use their saved URL and secret.",
          )}
        >
          <Confirm
            subject={subscription.name}
            title={t("Delete Webhook")}
            description={t(
              "Stops new notifications. Queued deliveries still use their saved URL and secret.",
            )}
            trigger={t("Delete")}
            triggerVariant="outline"
            danger
            retry={reload}
            action={() =>
              client
                .workspace(workspace.id)
                .DELETE("/api/v1/subscriptions/{subscription_id}", {
                  params: { path: { subscription_id: subscription.id } },
                  headers: ifMatch(rowTag(subscription)),
                })
            }
            onSuccess={() => {
              cache.removeQueries({
                queryKey: keys.subscription(subscription.id),
              });
              cache.removeQueries({
                queryKey: keys.deliveries(subscription.id),
              });
              void cache.invalidateQueries({ queryKey: keys.list });
              void navigate(`${basePath}/settings/webhooks`, { replace: true });
            }}
          />
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice
        error={toggle.error}
        retry={() => {
          toggle.reset();
          reload();
        }}
      />
    </div>
  );
}
