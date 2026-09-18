import { SettingsRow, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Section } from "../../shared/page";
import { AccountCredentials } from "../application-accounts/credentials";
import { AccountForm } from "../application-accounts/form";
import type { BotAccount } from "./account";
import { MemorySettings } from "./memory-settings";
import styles from "./bots.module.css";

/**
 * Settings for a saved bot: one level of group headings, each group on a
 * surface, separated by whitespace.
 */
export function BotSettings({
  account,
  reload,
  onMemoryConfigured,
  onCancel,
}: {
  account: BotAccount;
  reload: () => Promise<void>;
  onMemoryConfigured: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  const github = account.provider_key === "github";
  const manage = can("application_account.manage");
  if (!manage && !(!github && can("bot_memory.read")))
    return (
      <Section
        title={t("Settings")}
        description={t(
          "Bot settings are available to workspace administrators.",
        )}
      >
        <></>
      </Section>
    );
  return (
    <div className={styles.settings}>
      {manage && (
        <AccountForm
          bot
          initial={account}
          reload={reload}
          onCancel={onCancel}
          onSuccess={() => void reload()}
        />
      )}
      {!github && can("bot_memory.read") && (
        <SettingsSection
          title={t("Memory")}
          description={t(
            "Choose storage and control memory access for all groups connected to this bot. Configure individual groups in Memory.",
          )}
        >
          <SettingsRow
            label={t("Group memory storage")}
            description={t(
              account.memory
                ? "Bot-wide memory permissions. Each group can further restrict access."
                : "Memory is not configured. Your bot can still participate in conversations.",
            )}
          >
            <MemorySettings
              account={account}
              reload={reload}
              onConfigured={onMemoryConfigured}
            />
          </SettingsRow>
        </SettingsSection>
      )}
      {manage && (
        <SettingsSection
          title={t("Credentials")}
          description={t(
            "Existing credentials are never displayed. Supply a complete replacement.",
          )}
        >
          <AccountCredentials account={account} reload={reload} />
        </SettingsSection>
      )}
    </div>
  );
}
