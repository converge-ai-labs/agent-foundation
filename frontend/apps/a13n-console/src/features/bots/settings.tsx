import { SettingsRow, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Section } from "../../shared/page";
import { AccountCredentials } from "../application-accounts/credentials";
import { AccountForm } from "../application-accounts/form";
import type { BotAccount } from "./account";
import { MemorySettings } from "./memory-settings";

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
  return (
    <>
      {manage && (
        <Section
          title={t("Reception and routing")}
          description={t(
            "Who answers, where this bot listens, and how it replies.",
          )}
        >
          <AccountForm
            bot
            initial={account}
            reload={reload}
            onCancel={onCancel}
            onSuccess={() => void reload()}
          />
        </Section>
      )}
      {!github && can("bot_memory.read") && (
        <Section
          title={t("Memory")}
          description={t(
            "Choose storage and control memory access for all groups connected to this bot. Configure individual groups in Memory.",
          )}
        >
          <SettingsSection>
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
        </Section>
      )}
      {manage && (
        <Section
          title={t("Credentials")}
          description={t(
            "Existing credentials are never displayed. Supply a complete replacement.",
          )}
        >
          <AccountCredentials account={account} reload={reload} />
        </Section>
      )}
      {!manage && (
        <Section
          title={t("Settings")}
          description={t(
            "Bot settings are available to workspace administrators.",
          )}
        >
          <></>
        </Section>
      )}
    </>
  );
}
