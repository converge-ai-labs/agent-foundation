import { botAccount } from "./account";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { StateBadge, Timestamp } from "../../shared/feedback";
import { BotChecks } from "./checks";
import { LatestBotTest } from "./test-observation";
import { conditions, stages } from "./summary-labels";
import {
  messagingPolicy,
  responseLabels,
  placementLabels,
} from "../application-accounts/messaging-fields";
import styles from "./bots.module.css";

export function BotOverview({ summary }: { summary: Schema["BotSummary"] }) {
  const { t } = useTranslation(),
    { basePath, can } = useWorkspace(),
    account = botAccount(summary);
  const admin = can("application_account.manage"),
    policy = messagingPolicy(account.provider_policy);
  const settings = `${basePath}/bots/${account.id}/settings`,
    groups = `${basePath}/bots/${account.id}/channels`;
  const tasks: {
    text: string;
    label: string;
    href: string;
    allowed: boolean;
  }[] = [];
  if (account.status !== "active")
    tasks.push({
      text: "This application account is disabled. Enable it before receiving messages.",
      label: "Review account availability",
      href: `${basePath}/application-accounts/${account.id}`,
      allowed: admin,
    });
  if (!account.default_agent_id)
    tasks.push({
      text: "Select the agent that handles incoming messages.",
      label: "Choose an agent",
      href: settings,
      allowed: admin,
    });
  if (!account.execution_service_account_id)
    tasks.push({
      text: "Select the service account that authorizes incoming execution.",
      label: "Configure execution permissions",
      href: settings,
      allowed: admin,
    });
  if (
    account.reception_scope === "configured_targets" &&
    summary.configured_target_count === 0
  )
    tasks.push({
      text: "Add a conversation before enabling reception for configured targets.",
      label: "Configure conversations",
      href: groups,
      allowed: can("account_target.manage"),
    });
  if (
    summary.setup_condition === "needs_verification" ||
    summary.setup_condition === "check_failed"
  )
    tasks.push({
      text:
        summary.setup_condition === "check_failed"
          ? "The latest provider verification failed. Review its result below."
          : "Verify the current application credentials and external installation.",
      label: "Review application settings",
      href: `${basePath}/application-accounts/${account.id}`,
      allowed: admin,
    });
  if (!account.receive_enabled)
    tasks.push({
      text: "Reception is off. Complete setup before sending a test message.",
      label: "Resume setup",
      href: `${basePath}/bots/connect?account=${account.id}`,
      allowed: admin,
    });
  return (
    <>
      <section
        className={styles.overviewStatus}
        aria-label={t("Setup condition")}
      >
        <div>
          <strong>{t(conditions[summary.setup_condition])}</strong>
          {summary.checked_at && (
            <p>
              {t("Last checked")}
              {" · "}
              <Timestamp value={summary.checked_at} />
            </p>
          )}
        </div>
        <div>
          <strong>
            {t(
              summary.test_stage
                ? stages[summary.test_stage]
                : "No setup test recorded",
            )}
          </strong>
          {summary.test_observed_at && (
            <p>
              {t("Test observation")}
              {" · "}
              <Timestamp value={summary.test_observed_at} />
            </p>
          )}
        </div>
      </section>
      {!!tasks.length && (
        <section className={styles.setupTasks} aria-label={t("Complete setup")}>
          <h2>{t("Complete setup")}</h2>
          <ul>
            {tasks.map((task) => (
              <li key={task.text}>
                <span>{t(task.text)}</span>
                {task.allowed && <Link to={task.href}>{t(task.label)}</Link>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className={styles.overview}>
        <section>
          <h2>{t("Reception")}</h2>
          <StateBadge
            state={account.receive_enabled ? "enabled" : "disabled"}
          />
          <p>
            {t(
              account.reception_scope === "configured_targets"
                ? "Only configured conversations can trigger this bot."
                : "All accessible conversations may trigger this bot.",
            )}
          </p>
          <dl className={styles.overviewFacts}>
            <dt>{t("Default agent")}</dt>
            <dd>
              {account.default_agent_id ? (
                <Link to={`${basePath}/agents/${account.default_agent_id}`}>
                  {account.default_agent_id}
                </Link>
              ) : (
                t("Not configured")
              )}
            </dd>
            <dt>{t("When to respond")}</dt>
            <dd>
              {policy
                ? t(responseLabels[policy.interaction_mode])
                : t("Not configured")}
            </dd>
            <dt>{t("Reply placement")}</dt>
            <dd>
              {policy
                ? t(placementLabels[policy.reply_mode])
                : t("Not configured")}
            </dd>
            <dt>{t("Configured conversations")}</dt>
            <dd>
              <Link to={groups}>{summary.configured_target_count}</Link>
            </dd>
          </dl>
        </section>
        <section>
          <h2>{t("Conversation memory")}</h2>
          <p>
            {t(
              account.memory
                ? "Groups keep separate memory unless you explicitly share it."
                : "Memory is not configured. Your bot can still participate in conversations.",
            )}
          </p>
          {account.memory && (
            <dl className={styles.overviewFacts}>
              <dt>{t("Use memory during conversations")}</dt>
              <dd>{t(account.memory.use_memory ? "Enabled" : "Disabled")}</dd>
              <dt>{t("Allow explicit save and forget requests")}</dt>
              <dd>
                {t(account.memory.save_on_request ? "Enabled" : "Disabled")}
              </dd>
            </dl>
          )}
          <p>
            {t("Memory management is available to workspace administrators.")}
          </p>
          {admin && (
            <Link to={`${basePath}/bots/${account.id}/memory`}>
              {t("Manage memory")}
            </Link>
          )}
        </section>
      </div>
      <BotChecks account={account} />
      <div className={styles.overviewTest}>
        <LatestBotTest account={account} />
      </div>
      <Link to={`${basePath}/application-accounts/${account.id}`}>
        {t("View application account")}
      </Link>
    </>
  );
}
