import { Button } from "a13n-ui";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import { Section } from "../../shared/page";
import { usePlatformName } from "../integrations/platform";
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

export function checkErrorMessage(code: string) {
  return (
    checkErrors[code] ??
    "The check could not be completed. Review the account settings and try again."
  );
}

/**
 * The single "check connection" behaviour. Every surface that verifies a bot
 * against its platform reads and runs the check through this hook.
 */
export function useBotCheck({
  account,
  conversationId,
}: {
  account: Schema["Account"];
  conversationId?: string;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace();
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
  return {
    query,
    check,
    result,
    running: check.isPending || query.isFetching,
  };
}

/** Label used by every control that runs the platform check. */
export function useCheckLabel() {
  const { t } = useTranslation();
  return (running: boolean) => t(running ? "Checking…" : "Check connection");
}

export function BotChecks({
  account,
  conversationId,
  showAccountLink = false,
}: {
  account: Schema["Account"];
  conversationId?: string;
  showAccountLink?: boolean;
}) {
  const { can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    platformName = usePlatformName();
  const { query, check, result, running } = useBotCheck({
    account,
    conversationId,
  });
  const label = useCheckLabel();
  const identity = result?.installation,
    conversation = result?.conversation;
  return (
    <Section
      title={t(conversationId ? "Conversation access" : "Platform connection")}
      description={t(
        conversationId
          ? "Checks whether the bot can access this conversation. No message is sent."
          : "Checks the bot identity, connected organization, and whether the app is enabled. No message is sent.",
      )}
      actions={
        can("application_account.manage") && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={running}
            loading={check.isPending}
            onClick={() => check.mutate()}
          >
            {label(check.isPending)}
          </Button>
        )
      }
    >
      <ErrorNotice
        error={query.error || check.error}
        retry={() => void query.refetch()}
      />
      {query.isPending && <Loading variant="list" rows={2} />}
      {result ? (
        <div className={styles.checkResult}>
          {result.error_code && (
            <p className={styles.notice} data-tone="danger" role="status">
              {t(checkErrorMessage(result.error_code))}
            </p>
          )}
          {identity && (
            <dl className={styles.facts}>
              <div>
                <dt>
                  {t("Organization")} · {platformName(account.provider_key)}
                </dt>
                <dd>
                  <strong>{identity.organization_name}</strong>
                  <CopyableId value={identity.organization_id} />
                </dd>
              </div>
              <div>
                <dt>{t("Bot account")}</dt>
                <dd>
                  {showAccountLink ? (
                    <Link
                      to={`${basePath}/application-accounts/${account.id}`}
                      title={t("View application account")}
                    >
                      <strong>{identity.bot_name}</strong>
                    </Link>
                  ) : (
                    <strong>{identity.bot_name}</strong>
                  )}
                  <CopyableId value={identity.bot_id} />
                </dd>
              </div>
              <div>
                <dt>{t("App status")}</dt>
                <dd>
                  <StatePill
                    state={identity.enabled ? "active" : "inactive"}
                    label={t(identity.enabled ? "Active" : "Inactive")}
                  />
                </dd>
              </div>
            </dl>
          )}
          {conversation && (
            <dl className={styles.facts}>
              <div>
                <dt>{t("Conversation")}</dt>
                <dd>
                  <strong>{conversation.name}</strong>
                  <CopyableId value={conversation.id} />
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
          <p className={styles.hint}>
            {t("Last checked")} <Timestamp value={result.checked_at} relative />
          </p>
          <p className={styles.hint}>
            {t(
              "This result reflects the last check. It does not confirm that messages reach the bot, the agent runs, or replies are delivered.",
            )}
          </p>
        </div>
      ) : (
        !query.isPending &&
        !query.error && (
          <p className={styles.hint}>
            {t("No check is available for the current credentials.")}
          </p>
        )
      )}
    </Section>
  );
}
