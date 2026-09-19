import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { Section } from "../../shared/page";
import styles from "./bots.module.css";

const labels = {
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
  disabled: "Disabled",
  http: "HTTP callback",
} as const;

/** The pill hue names how healthy the event link is, not just its name. */
const states = {
  connecting: "pending",
  connected: "active",
  reconnecting: "pending",
  disconnected: "failed",
  disabled: "disabled",
  http: "inactive",
} as const;

export function EventConnection({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { t } = useTranslation();
  const slack = account.provider_key === "slack";
  const query = useQuery({
    queryKey: ["event-connection", account.id, account.version],
    refetchInterval: 5000,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/event-connection", {
          params: { path: { account_id: account.id } },
          signal,
        })
        .then(data),
  });
  return (
    <Section
      title={t(slack ? "Socket Mode" : "Long connection (WebSocket)")}
      description={t(
        slack
          ? "Enable Socket Mode and subscribe to message events in your Slack app settings."
          : "In Feishu app settings, choose long connection for event subscriptions, add the message receive event, and publish the app version.",
      )}
      actions={
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          <ArrowClockwiseIcon aria-hidden="true" />
          {t("Refresh")}
        </Button>
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading variant="list" rows={1} />}
      {query.data && (
        <dl className={styles.facts}>
          <div>
            <dt>{t("Event connection")}</dt>
            <dd>
              <StatePill
                state={states[query.data.state]}
                label={t(labels[query.data.state])}
              />
            </dd>
          </div>
        </dl>
      )}
      {query.data?.error_code && (
        <p className={styles.notice} data-tone="danger" role="status">
          {t(
            "Connection failed. Check the app credentials, platform connection mode, and network access.",
          )}
        </p>
      )}
      <p className={styles.hint}>
        {t(
          "No public callback URL is needed. The Connectivity service must remain running.",
        )}
      </p>
      <p className={styles.hint}>
        {t(
          "Connected confirms the event connection only. Use the setup test to verify message reception, agent execution, and replies.",
        )}
      </p>
    </Section>
  );
}
