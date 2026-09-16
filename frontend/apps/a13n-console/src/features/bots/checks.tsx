import { PlugsConnectedIcon, ArrowRightIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import styles from "./bots.module.css";

const checkErrors: Record<string, string> = {
  bot_identity_mismatch:
    "These credentials belong to a different app, bot, or workspace. Check the application account identity.",
  bot_inactive:
    "The provider reports that this bot is not active. Enable or publish the app on the provider before continuing.",
  credential_unavailable:
    "The account credentials could not be read. Replace them and check again.",
  provider_rejected:
    "The provider rejected the check. Review the app credentials and required permissions.",
  provider_unavailable: "The provider did not respond. Try the check again.",
  rate_limited:
    "The provider is limiting requests. Wait before checking again.",
  invalid_provider_response:
    "The provider response could not be verified. No access has been confirmed.",
};

export function BotChecks({
  account,
  conversationId,
  showAccountLink = false,
}: {
  account: Schema["Account"];
  conversationId?: string;
  showAccountLink?: boolean;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can, workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const key = [
    "bot-check",
    workspace.id,
    account.id,
    account.credential_generation,
    conversationId ?? null,
  ];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/checks/latest", {
          params: {
            path: { account_id: account.id },
            query: { conversation_id: conversationId },
          },
          signal,
        })
        .then(data),
  });
  const check = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/application-accounts/{account_id}/bot/checks", {
          params: { path: { account_id: account.id } },
          body: {
            expected_version: account.version,
            conversation_id: conversationId,
          },
        })
        .then(data),
    onSuccess: () =>
      Promise.all([
        cache.invalidateQueries({ queryKey: key }),
        cache.invalidateQueries({ queryKey: ["bots", workspace.id] }),
        cache.invalidateQueries({
          queryKey: [
            "application-accounts",
            workspace.id,
            account.id,
            "bot-summary",
          ],
        }),
      ]),
  });
  const result =
    !query.error && !query.isFetching ? query.data?.latest : undefined;
  const identity = result?.installation,
    conversation = result?.conversation;
  return (
    <section
      className={styles.checkSection}
      aria-label={t(
        conversationId ? "Conversation access" : "Platform connection status",
      )}
    >
      <div className={styles.memoryHeading}>
        <div>
          <h2 className={styles.sectionTitle}>
            <PlugsConnectedIcon aria-hidden="true" />
            {t(
              conversationId
                ? "Conversation access"
                : "Platform connection status",
            )}
          </h2>
          <p>
            {t(
              conversationId
                ? "Checks whether the bot can access this conversation. No message is sent."
                : "Checks the bot identity, connected workspace or enterprise, and whether the app is enabled. No message is sent.",
            )}
          </p>
        </div>
        {can("application_account.manage") && (
          <Button
            type="button"
            variant="outline"
            disabled={check.isPending || query.isFetching}
            onClick={() => check.mutate()}
          >
            {t(check.isPending ? "Checking…" : "Check now")}
          </Button>
        )}
      </div>
      <ErrorNotice
        error={query.error || check.error}
        retry={() => void query.refetch()}
      />
      {query.isPending && <Loading />}
      {check.isPending && (
        <p role="status">
          {t("Checking the provider. This may take a few seconds.")}
        </p>
      )}
      {result ? (
        <div className={styles.checkResult}>
          <p>
            <strong>{t("Last checked")}</strong>{" "}
            <time dateTime={result.checked_at}>
              {new Date(result.checked_at).toLocaleString()}
            </time>
          </p>
          {result.error_code && (
            <p role="status">
              {t(
                checkErrors[result.error_code] ??
                  "The check could not be completed. Review the account settings and try again.",
              )}
            </p>
          )}
          {identity && (
            <dl className={styles.overviewFacts}>
              <div>
                <dt>
                  {t(
                    account.provider_key === "slack"
                      ? "Slack workspace"
                      : "Feishu enterprise",
                  )}
                </dt>
                <dd>
                  <strong>{identity.organization_name}</strong>
                  <code>{identity.organization_id}</code>
                </dd>
              </div>
              <div>
                <dt>{t("Bot account")}</dt>
                <dd>
                  {showAccountLink ? (
                    <Link
                      className={styles.factLink}
                      to={`${basePath}/application-accounts/${account.id}`}
                      title={t("View application account")}
                    >
                      <strong>{identity.bot_name}</strong>
                      <ArrowRightIcon aria-hidden="true" />
                    </Link>
                  ) : (
                    <strong>{identity.bot_name}</strong>
                  )}{" "}
                  <code>{identity.bot_id}</code>
                </dd>
              </div>
              <div>
                <dt>{t("App status")}</dt>
                <dd>
                  <StateBadge
                    state={identity.enabled ? "active" : "inactive"}
                    label={t(identity.enabled ? "Active" : "Inactive")}
                  />
                </dd>
              </div>
            </dl>
          )}
          {conversation && (
            <dl className={styles.overviewFacts}>
              <div>
                <dt>{t("Conversation")}</dt>
                <dd>
                  {conversation.name} <code>{conversation.id}</code>
                </dd>
              </div>
              <div>
                <dt>{t("Bot membership")}</dt>
                <dd>
                  {t(
                    conversation.is_member === true
                      ? "Joined"
                      : conversation.is_member === false
                        ? "Not a member"
                        : "Unknown",
                  )}
                </dd>
              </div>
              <div>
                <dt>{t("Visibility")}</dt>
                <dd>
                  {t(
                    {
                      public: "Public",
                      private: "Private",
                      direct: "Direct conversation",
                      unknown: "Unknown",
                    }[conversation.audience],
                  )}
                </dd>
              </div>
            </dl>
          )}
          <p>
            {t(
              "This result reflects the last check. It does not confirm that messages reach the bot, the agent runs, or replies are delivered.",
            )}
          </p>
        </div>
      ) : (
        !query.isPending &&
        !query.error && (
          <p>{t("No check is available for the current credentials.")}</p>
        )
      )}
    </section>
  );
}
