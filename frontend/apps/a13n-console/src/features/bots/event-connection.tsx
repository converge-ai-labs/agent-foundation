import { Badge, Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";

const states = {
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
  disabled: "Disabled",
  http: "HTTP callback",
} as const;

export function EventConnection({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { t } = useTranslation();
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
    <section>
      <h2>
        {t(
          account.provider_key === "slack"
            ? "Socket Mode"
            : "Long connection (WebSocket)",
        )}
      </h2>
      <p>
        {t(
          account.provider_key === "slack"
            ? "Enable Socket Mode and subscribe to message events in your Slack app settings."
            : "In Feishu app settings, choose long connection for event subscriptions, add the message receive event, and publish the app version.",
        )}
      </p>
      <p>
        {t(
          "No public callback URL is needed. The Connectivity service must remain running.",
        )}
      </p>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {query.data && (
        <>
          <Badge>{t(states[query.data.state])}</Badge>
          {query.data.error_code && (
            <p role="status">
              {t(
                "Connection failed. Check the app credentials, platform connection mode, and network access.",
              )}
            </p>
          )}
          <p>
            {t(
              "Connected confirms the event connection only. Use the setup test to verify message reception, agent execution, and replies.",
            )}
          </p>
        </>
      )}
      <Button
        variant="secondary"
        onClick={() => void query.refetch()}
        disabled={query.isFetching}
      >
        {t("Refresh")}
      </Button>
    </section>
  );
}
