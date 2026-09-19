import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { Button, DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../shared/identity";
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
          "files:read",
          "files:write",
          "users:read",
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
      interactivity: { is_enabled: true },
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
    <div className={styles.instructions}>
      <h3>
        {t(
          platform === "slack"
            ? "Connect your Slack app"
            : "Connect your Feishu app",
        )}
      </h3>
      <p>
        {t(
          "Attach images, PDFs, or UTF-8 text files to a message that triggers the bot (up to 5 files, 20 MiB each). Image and PDF reading depends on the selected model or environment. To return generated files, the agent needs an environment and the Publish asset tool.",
        )}
      </p>
      {platform === "slack" ? (
        <>
          <ol>
            <li>
              {t(
                "Create an app in your own Slack workspace, enable its bot, and install it in the workspace you want to connect.",
              )}
            </li>
            <li>
              {t(
                "Choose HTTP callbacks or Socket Mode below. HTTP uses a signing secret; Socket Mode uses an app-level token with connections:write. Both use the bot token. Enable Interactivity so requesters can stop tasks. For HTTP, set its Request URL to the same URL as Event Subscriptions; Socket Mode receives interactions over the connection.",
              )}
            </li>
            <li>
              {t(
                "Invite the bot to your pilot channel. If you change OAuth scopes, reinstall or reauthorize the Slack app; workspace approval may be required.",
              )}
            </li>
          </ol>
          <p>
            {t(
              "For image and file input, grant files:read; for generated file replies, grant files:write. Reauthorize existing installations after adding these scopes.",
            )}
          </p>
          <Button
            variant="outline"
            size="sm"
            render={
              <a
                href="https://api.slack.com/apps"
                target="_blank"
                rel="noreferrer"
              />
            }
          >
            {t("Open Slack app settings")}
            <ArrowSquareOutIcon size={13} aria-hidden="true" />
          </Button>
          <DisclosureSection title={t("Slack manifest template")}>
            <p>
              {t(
                "This template includes public and private channel history, direct messages, and group direct messages for discussion and chat modes. Remove unused event subscriptions and their scopes for a mention-only pilot. Keep users:read for bot identity verification. For HTTP add the request URL after saving; for Socket Mode enable socket_mode_enabled and create an app-level token.",
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
          <ol>
            <li>
              {t(
                "Create an enterprise custom app in Feishu and enable Bot capability.",
              )}
            </li>
            <li>
              {t(
                "Grant the message permissions for your selected interaction mode. In Events and Callbacks, subscribe to im.message.receive_v1 under Event configuration, then add card.action.trigger under Callback configuration using the same connection method. Publish these changes so requesters can stop tasks from progress cards.",
              )}
            </li>
            <li>
              {t(
                "Provide App ID and App Secret. For HTTP, also provide the verification token and optional encryption key. Save here, then finish the event connection in Feishu.",
              )}
            </li>
            <li>
              {t(
                "Publish an app version with the intended availability range, then add the bot through the group's settings. An unpublished or unavailable app may not appear in search.",
              )}
            </li>
          </ol>
          <p>
            {t(
              "Enable message-resource access to read attachments. To send generated files, grant im:resource (or an existing im:resource:upload permission), then publish the updated Feishu app permissions.",
            )}
          </p>
          <p>
            {t(
              "To receive standalone group file messages, Feishu requires im:message.group_msg and a reception mode that accepts messages without a mention. This permission gives the app access to all messages in groups it joins; enable it only when needed.",
            )}
          </p>
          <Button
            variant="outline"
            size="sm"
            render={
              <a
                href="https://open.feishu.cn/app"
                target="_blank"
                rel="noreferrer"
              />
            }
          >
            {t("Open Feishu app settings")}
            <ArrowSquareOutIcon size={13} aria-hidden="true" />
          </Button>
        </>
      )}
    </div>
  );
}
