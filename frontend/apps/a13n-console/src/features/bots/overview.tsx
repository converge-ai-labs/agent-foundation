import { ArrowRightIcon, CircleIcon } from "@phosphor-icons/react";
import { Button, SettingsRow, SettingsSection } from "a13n-ui";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ListRow, ListRows } from "../../shared/collection";
import { StatePill, Timestamp } from "../../shared/feedback";
import { DetailLayout, RailRow, RailSection, Section } from "../../shared/page";
import { useAgent } from "../agents/queries";
import { ReceptionPill, SetupPill, testStages } from "../integrations/platform";
import { botAccount } from "./account";
import { BotChecks, useBotCheck } from "./checks";
import { CallbackSetup } from "./event-setup";
import { LatestBotTest } from "./test-observation";
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
  const check = useBotCheck({ account });
  const settings = `${basePath}/bots/${account.id}/settings`,
    channels = `${basePath}/bots/${account.id}/channels`;
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
      href: channels,
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
  const agentValue = !account.default_agent_id ? (
    <span className={styles.unset}>{t("Not configured")}</span>
  ) : agent.data && !agent.error ? (
    <Link to={`${basePath}/agents/${agent.data.key}`}>{agent.data.name}</Link>
  ) : (
    <span className={styles.unset}>
      {t(agent.isPending ? "Loading…" : "Agent unavailable")}
    </span>
  );
  const rail = (
    <>
      <RailSection title={t("Status")}>
        <RailRow label={t("Availability")}>
          <StatePill state={account.status} />
        </RailRow>
        <RailRow label={t("Setup")}>
          <SetupPill condition={summary.setup_condition} />
        </RailRow>
        <RailRow label={t("Message responses")}>
          <ReceptionPill enabled={!!account.receive_enabled} />
        </RailRow>
        <RailRow label={t("Platform connection")}>
          {check.result ? (
            <StatePill
              state={
                check.result.error_code
                  ? "failed"
                  : check.result.installation?.enabled
                    ? "active"
                    : "inactive"
              }
              label={t(
                check.result.error_code
                  ? "Check failed"
                  : check.result.installation?.enabled
                    ? "Reachable"
                    : "Inactive",
              )}
            />
          ) : (
            <span className={styles.unset}>{t("Not checked")}</span>
          )}
        </RailRow>
        {summary.checked_at && (
          <RailRow label={t("Last checked")}>
            <Timestamp value={summary.checked_at} relative />
          </RailRow>
        )}
        <RailRow label={t("Setup test")}>
          <span>
            {t(
              summary.test_stage
                ? testStages[summary.test_stage]
                : "No setup test recorded",
            )}
          </span>
        </RailRow>
      </RailSection>
      {!github && (
        <RailSection title={t("Group memory")}>
          <RailRow label={t("Storage")}>
            <StatePill
              state={account.memory ? "enabled" : "disabled"}
              label={t(account.memory ? "Configured" : "Not configured")}
            />
          </RailRow>
          {account.memory && (
            <>
              <RailRow label={t("Referenced in answers")}>
                <ReceptionPill enabled={!!account.memory.use_memory} />
              </RailRow>
              <RailRow label={t("Editable from chat")}>
                <ReceptionPill enabled={!!account.memory.save_on_request} />
              </RailRow>
            </>
          )}
          {admin && (
            <RailRow label={t("Manage")}>
              <Link to={`${basePath}/bots/${account.id}/memory`}>
                {t("Manage memory")}
              </Link>
            </RailRow>
          )}
        </RailSection>
      )}
    </>
  );
  return (
    <DetailLayout rail={rail}>
      {!!tasks.length && (
        <Section
          title={t("Complete setup")}
          description={t(
            "What is still missing before this bot can answer in its conversations.",
          )}
        >
          <ListRows>
            {tasks.map((task) => (
              <ListRow
                key={task.text}
                icon={<CircleIcon size={14} aria-hidden="true" />}
                name={t(task.text)}
                actions={
                  task.allowed && (
                    <Button
                      size="sm"
                      variant="outline"
                      render={<Link to={task.href} />}
                    >
                      {t(task.label)}
                    </Button>
                  )
                }
              />
            ))}
          </ListRows>
        </Section>
      )}
      <Section
        title={t("Reception")}
        description={t(
          account.reception_scope === "configured_targets"
            ? "Only configured conversations can trigger this bot."
            : "All accessible conversations may trigger this bot.",
        )}
        actions={
          admin && (
            <Button size="sm" variant="outline" render={<Link to={settings} />}>
              {t("Edit")}
            </Button>
          )
        }
      >
        <SettingsSection>
          <SettingsRow label={t("Default agent")}>{agentValue}</SettingsRow>
          <SettingsRow label={t("When to respond")}>
            {github
              ? t(
                  account.provider_config_version === "github_notifications_v1"
                    ? "Notification updates"
                    : "Selected GitHub events",
                )
              : policy
                ? t(responseLabels[policy.interaction_mode])
                : t("Not configured")}
          </SettingsRow>
          <SettingsRow
            label={t("Reply placement")}
            description={
              policy?.reply_mode === "auto"
                ? t(automaticPlacementHint)
                : undefined
            }
          >
            {github
              ? t("Issue or PR comment")
              : policy
                ? t(placementLabels[policy.reply_mode])
                : t("Not configured")}
          </SettingsRow>
          <SettingsRow
            label={t(
              github ? "Configured repositories" : "Configured conversations",
            )}
          >
            <Link to={channels} className={styles.countLink}>
              {summary.configured_target_count}
              <ArrowRightIcon size={12} aria-hidden="true" />
            </Link>
          </SettingsRow>
        </SettingsSection>
      </Section>
      {(account.provider_config.event_transport === "websocket" ||
        (github && admin)) && <CallbackSetup account={account} />}
      <BotChecks account={account} showAccountLink />
      <LatestBotTest account={account} />
    </DetailLayout>
  );
}
