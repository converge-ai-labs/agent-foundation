import { ChoiceField } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";

import { useTranslation } from "react-i18next";
import { useState } from "react";
import {
  readLanguagePreference,
  resolveLanguage,
  saveLanguage,
} from "../../i18n/preferences";
import { useAppearance } from "../../layout/appearance";
import styles from "./settings.module.css";

export function Preferences() {
  const { t } = useTranslation(),
    { theme, setTheme } = useAppearance();
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
      <p className={styles.note}>
        {t("Preferences are saved automatically in this browser.")}
      </p>
    </div>
  );
}

export function LanguageSettings() {
  const { t, i18n } = useTranslation();
  const [language, setLanguage] = useState(readLanguagePreference);
  const changeLanguage = (value: string) => {
    if (value !== "system" && value !== "en" && value !== "zh-CN") return;
    setLanguage(value);
    saveLanguage(value);
    void i18n.changeLanguage(resolveLanguage(value));
  };
  return (
    <div className={styles.sections}>
      <SettingsSection variant="outlined">
        <SettingsRow label={t("Display language")}>
          <div className={styles.control}>
            <ChoiceField
              placeholder={t("Language")}
              value={language}
              onValueChange={changeLanguage}
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
