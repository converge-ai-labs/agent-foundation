import { ChoiceField, SettingsRow, SettingsSection } from "a13n-ui";

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
          <ChoiceField
            className="w-auto min-w-0"
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
              { value: "system", label: t("Follow system") },
            ]}
          />
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
    if (
      value !== "system" &&
      value !== "en" &&
      value !== "zh-CN" &&
      value !== "hi"
    )
      return;
    setLanguage(value);
    saveLanguage(value);
    void i18n.changeLanguage(resolveLanguage(value));
  };
  return (
    <div className={styles.sections}>
      <SettingsSection variant="outlined">
        <SettingsRow label={t("Display language")}>
          <ChoiceField
            className="w-auto min-w-0"
            placeholder={t("Language")}
            value={language}
            onValueChange={changeLanguage}
            label={t("Display language")}
            hideLabel
            options={[
              { value: "system", label: t("System") },
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
              { value: "hi", label: "हिन्दी" },
            ]}
          />
        </SettingsRow>
      </SettingsSection>
      <p className={styles.note}>
        {t("Preferences are saved automatically in this browser.")}
      </p>
    </div>
  );
}
