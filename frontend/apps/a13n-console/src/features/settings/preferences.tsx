import { ChoiceField } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";

import { useTranslation } from "react-i18next";
import { useAppearance } from "../../layout/appearance";
import styles from "./settings.module.css";

export function Preferences() {
  const { t, i18n } = useTranslation(),
    { theme, setTheme } = useAppearance();
  return (
    <div className={styles.sections}>
      <SettingsSection title={t("Appearance")}>
        <SettingsRow label={t("Color theme")}>
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
        </SettingsRow>
      </SettingsSection>
      <SettingsSection title={t("Language and region")}>
        <SettingsRow
          label={t("Display language")}
          description={t("Used for navigation, dates, and controls.")}
        >
          <ChoiceField
            placeholder={t("Language")}
            value={i18n.resolvedLanguage ?? "en"}
            onValueChange={(value) => void i18n.changeLanguage(value)}
            label={t("Display language")}
            hideLabel
            options={[
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
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
