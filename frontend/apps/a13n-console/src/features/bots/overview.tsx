import { Button } from "a13n-ui";
import {
  ArrowRightIcon,
  ChatCircleDotsIcon,
  BrainIcon,
} from "@phosphor-icons/react";
import { botAccount } from "./account";
import { useAgent } from "../agents/queries";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { StatePill, Timestamp } from "../../shared/feedback";
import { EventConnection } from "./event-connection";
import { CallbackSetup } from "./connect";
import { BotChecks } from "./checks";
import { LatestBotTest } from "./test-observation";
import { conditions, stages } from "./summary-labels";
import {
  messagingPolicy,
  responseLabels,
  placementLabels,
  automaticPlacementHint,
} from "../application-accounts/messaging-fields";
import styles from "./bots.module.css";

export function BotOverview({ summary }: { summary: Schema["BotSummary"] }) {
  const { t } = useTranslation(),
    { basePath, can } = useWorkspace(),
    account = botAccount(summary);
  const github = account.provider_key === "github";
  const agent = useAgent(account.default_agent_id ?? undefined);
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
          <header className={styles.overviewCardHeader}>
            <h2>
              <ChatCircleDotsIcon aria-hidden="true" />
              {t("Message responses")}
            </h2>
            <StatePill
              state={account.receive_enabled ? "enabled" : "disabled"}
            />
          </header>
          <p>
            {t(
              account.reception_scope === "configured_targets"
                ? "Only configured conversations can trigger this bot."
                : "All accessible conversations may trigger this bot.",
            )}
          </p>
          <dl className={styles.overviewFacts}>
            <div>
              <dt>{t("Default agent")}</dt>
              <dd>
                {!account.default_agent_id ? (
                  t("Not configured")
                ) : agent.data && !agent.error ? (
                  <Link
                    className={styles.factLink}
                    to={`${basePath}/agents/${agent.data.key}`}
                  >
                    {agent.data.name}
                    <ArrowRightIcon aria-hidden="true" />
                  </Link>
                ) : (
                  t(agent.isPending ? "Loading…" : "Agent unavailable")
                )}
              </dd>
            </div>
            <div>
              <dt>{t("When to respond")}</dt>
              <dd>
                <span className={styles.factValue}>
                  {github
                    ? t(
                        account.provider_config_version ===
                          "github_notifications_v1"
                          ? "Notification updates"
                          : "Selected GitHub events",
                      )
                    : policy
                      ? t(responseLabels[policy.interaction_mode])
                      : t("Not configured")}
                </span>
              </dd>
            </div>
            <div>
              <dt>{t("Reply placement")}</dt>
              <dd>
                <span className={styles.factValue}>
                  {github
                    ? t("Issue or PR comment")
                    : policy
                      ? t(placementLabels[policy.reply_mode])
                      : t("Not configured")}
                </span>
                {policy?.reply_mode === "auto" && (
                  <small className={styles.factHint}>
                    {t(automaticPlacementHint)}
                  </small>
                )}
              </dd>
            </div>
            <div>
              <dt>
                {t(
                  github
                    ? "Configured repositories"
                    : "Configured conversations",
                )}
              </dt>
              <dd>
                <Link className={styles.factLink} to={groups}>
                  {summary.configured_target_count}
                  <ArrowRightIcon aria-hidden="true" />
                </Link>
              </dd>
            </div>
          </dl>
        </section>
        {!github && (
          <section>
            <header className={styles.overviewCardHeader}>
              <h2>
                <BrainIcon aria-hidden="true" />
                {t("Group memory")}
              </h2>
            </header>
            <p>
              {t(
                account.memory
                  ? "Bot-wide memory permissions. Each group can further restrict access and choose who can read its memory."
                  : "Memory is not configured. Your bot can still participate in conversations.",
              )}
            </p>
            {account.memory && (
              <dl className={styles.overviewFacts}>
                <div>
                  <dt>{t("Refer to memory when answering")}</dt>
                  <dd>
                    <StatePill
                      state={account.memory.use_memory ? "enabled" : "disabled"}
                    />
                  </dd>
                </div>
                <div>
                  <dt>{t("Allow saving or deleting memory through chat")}</dt>
                  <dd>
                    <StatePill
                      state={
                        account.memory.save_on_request ? "enabled" : "disabled"
                      }
                    />
                  </dd>
                </div>
              </dl>
            )}
            <footer className={styles.overviewCardFooter}>
              <p>
                {t(
                  "Memory management is available to workspace administrators.",
                )}
              </p>
              {admin && (
                <Button
                  variant="outline"
                  render={<Link to={`${basePath}/bots/${account.id}/memory`} />}
                >
                  {t("Manage memory")}
                  <ArrowRightIcon aria-hidden="true" />
                </Button>
              )}
            </footer>
          </section>
        )}
      </div>
      {account.provider_config.event_transport === "websocket" && (
        <div className={styles.eventConnection}>
          <EventConnection account={account} />
        </div>
      )}
      {github && admin && <CallbackSetup account={account} />}
      <BotChecks account={account} showAccountLink />
      <div className={styles.overviewTest}>
        <LatestBotTest account={account} />
      </div>
    </>
  );
}
