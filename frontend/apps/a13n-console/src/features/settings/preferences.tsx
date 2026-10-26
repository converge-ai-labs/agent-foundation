import { ChoiceField } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";

import { useTranslation } from "react-i18next";
import { useState } from "react";
import {
  readLanguagePreference,
  resolveLanguage,
  saveLanguage,
  type LanguagePreference,
} from "../../i18n/preferences";
import { useAppearance } from "../../layout/appearance";
import styles from "./settings.module.css";

export function Preferences() {
  const { t, i18n } = useTranslation(),
    { theme, setTheme } = useAppearance();
  const [languagePreference, setLanguagePreference] = useState(
    readLanguagePreference,
  );
  return (
    <div className={styles.sections}>
      <SettingsSection variant="outlined" title={t("Appearance")}>
        <SettingsRow label={t("Color theme")}>
          <div className={styles.control}>
            <ChoiceField
              label={t("Color theme")}
              hideLabel
              value={theme}
              onValueChange={(value) => {
                if (value === "light" || value === "dark" || value === "system")
                  setTheme(value);
              }}
              options={[
                { value: "light", label: t("Light") },
                { value: "dark", label: t("Dark") },
                { value: "system", label: t("System") },
              ]}
            />
          </div>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="outlined" title={t("Language and region")}>
        <SettingsRow
          label={t("Display language")}
          description={t("Used for navigation, dates, and controls.")}
        >
          <div className={styles.control}>
            <ChoiceField
              placeholder={t("Language")}
              value={languagePreference}
              onValueChange={(value) => {
                if (value !== "system" && value !== "en" && value !== "zh-CN")
                  return;
                const preference: LanguagePreference = value;
                setLanguagePreference(preference);
                saveLanguage(preference);
                void i18n.changeLanguage(resolveLanguage(preference));
              }}
              label={t("Display language")}
              hideLabel
              options={[
                { value: "system", label: t("System") },
                { value: "en", label: "English" },
                { value: "zh-CN", label: "简体中文" },
              ]}
            />
          </div>
        </SettingsRow>
      </SettingsSection>
      <p className={styles.note}>
        {t("Preferences are saved automatically in this browser.")}
      </p>
    </div>
  );
}
