import { Input } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { CopyButton } from "../../shared/identity";
import { Section } from "../../shared/page";
import { EventConnection } from "./event-connection";
import styles from "./bots.module.css";

/** How events reach the console for this account, whichever transport it uses. */
export function CallbackSetup({ account }: { account: Schema["Account"] }) {
  return account.provider_config.event_transport === "websocket" ? (
    <EventConnection account={account} />
  ) : (
    <HttpCallbackSetup account={account} />
  );
}

function HttpCallbackSetup({ account }: { account: Schema["Account"] }) {
  const client = useClient(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["bot-setup", account.workspace_id, account.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/setup", {
          params: { path: { account_id: account.id } },
          signal,
        })
        .then(data),
  });
  if (account.provider_config_version === "github_notifications_v1")
    return (
      <Section
        title={t("Notification polling")}
        description={t(
          "Only outbound GitHub access is required. Mention this account or subscribe it to an Issue or PR in a configured repository. Polling starts when reception is enabled.",
        )}
      >
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.data?.poll_checked_at && (
          <p className={styles.hint}>
            {t("Last checked")}{" "}
            <Timestamp value={query.data.poll_checked_at} relative />
          </p>
        )}
        {query.data?.poll_error_code && (
          <p className={styles.notice} data-tone="danger" role="status">
            {t("Polling failed")}: {query.data.poll_error_code}
          </p>
        )}
      </Section>
    );
  const endpoint = query.data?.event_url ?? query.data?.event_path ?? "";
  return (
    <Section
      title={t("Event endpoint")}
      description={t(
        "Set this event endpoint in your provider's app settings. Provider URL verification works while reception is disabled.",
      )}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading variant="list" rows={1} />}
      {query.data && (
        <>
          <div className={styles.copyField}>
            <Input readOnly value={endpoint} aria-label={t("Event endpoint")} />
            <CopyButton value={endpoint} copyLabel={t("Copy event endpoint")} />
          </div>
          {!query.data.event_url && (
            <p className={styles.notice} data-tone="warning" role="status">
              {t(
                "No public event origin is configured. Ask the deployment administrator to configure it before connecting the provider.",
              )}
            </p>
          )}
          <p className={styles.hint}>
            {t(
              "The provider must be able to reach this endpoint. A localhost address is only usable for local tests.",
            )}
          </p>
        </>
      )}
    </Section>
  );
}
