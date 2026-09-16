import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../shared/copy";
import styles from "./connect.module.css";

const slackManifest = JSON.stringify(
  {
    display_information: { name: "a13n bot" },
    features: { bot_user: { display_name: "a13n bot", always_online: false } },
    oauth_config: {
      scopes: {
        bot: [
          "app_mentions:read",
          "chat:write",
          "channels:read",
          "channels:history",
          "groups:read",
          "groups:history",
          "im:read",
          "im:history",
          "mpim:read",
          "mpim:history",
        ],
      },
    },
    settings: {
      event_subscriptions: {
        bot_events: [
          "app_mention",
          "message.channels",
          "message.groups",
          "message.im",
          "message.mpim",
        ],
      },
      socket_mode_enabled: false,
    },
  },
  null,
  2,
);

export function BotSetupInstructions({
  platform,
}: {
  platform: "slack" | "lark";
}) {
  const { t } = useTranslation();
  return (
    <section>
      <h2>
        {t(
          platform === "slack"
            ? "Connect your Slack app"
            : "Connect your Feishu app",
        )}
      </h2>
      {platform === "slack" ? (
        <>
          <ol className={styles.instructions}>
            <li>
              {t(
                "Create an app in your own Slack workspace, enable its bot, and install it in the workspace you want to connect.",
              )}
            </li>
            <li>
              {t(
                "Copy the bot token and signing secret. Save the account here, then add the event endpoint in Slack's Event Subscriptions.",
              )}
            </li>
            <li>
              {t(
                "Invite the bot to your pilot channel. If you change OAuth scopes, reinstall or reauthorize the Slack app; workspace approval may be required.",
              )}
            </li>
          </ol>
          <p>
            <a
              href="https://api.slack.com/apps"
              target="_blank"
              rel="noreferrer"
            >
              {t("Open Slack app settings")}
            </a>
          </p>
          <DisclosureSection title={t("Slack manifest template")}>
            <p>
              {t(
                "This template includes public and private channel history, direct messages, and group direct messages for discussion and chat modes. Remove unused event subscriptions and their scopes for a mention-only pilot. Add the HTTP request URL after saving the account.",
              )}
            </p>
            <CopyButton value={slackManifest} copyLabel={t("Copy manifest")} />
            <pre className={styles.manifest}>{slackManifest}</pre>
            <a
              href="https://docs.slack.dev/reference/app-manifest/"
              target="_blank"
              rel="noreferrer"
            >
              {t("Slack manifest documentation")}
            </a>
          </DisclosureSection>
        </>
      ) : (
        <>
          <ol className={styles.instructions}>
            <li>
              {t(
                "Create an enterprise custom app in Feishu and enable Bot capability.",
              )}
            </li>
            <li>
              {t(
                "Grant the message permissions for your selected interaction mode and subscribe to im.message.receive_v1 using HTTP events.",
              )}
            </li>
            <li>
              {t(
                "Provide the App secret, verification token, and optional encryption key. Save here, then configure the event endpoint in Feishu.",
              )}
            </li>
            <li>
              {t(
                "Publish an app version with the intended availability range, then add the bot through the group's settings. An unpublished or unavailable app may not appear in search.",
              )}
            </li>
          </ol>
          <p>
            <a
              href="https://open.feishu.cn/app"
              target="_blank"
              rel="noreferrer"
            >
              {t("Open Feishu app settings")}
            </a>
          </p>
        </>
      )}
    </section>
  );
}
