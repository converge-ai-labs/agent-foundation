import { Laptop, Moon, Sun } from "lucide-react";
import { Select, SettingsRow, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAppearance } from "../../layout/appearance";
import styles from "./settings.module.css";

export function Preferences() {
  const { t, i18n } = useTranslation(),
    { theme, setTheme } = useAppearance();
  return (
    <div className={styles.preferences}>
      <SettingsSection title={t("Appearance")}>
        <div
          className={styles.themeOptions}
          role="group"
          aria-label={t("Color theme")}
        >
          {(["light", "dark", "system"] as const).map((value) => {
            const Icon =
              value === "light" ? Sun : value === "dark" ? Moon : Laptop;
            return (
              <button
                type="button"
                key={value}
                aria-pressed={theme === value}
                onClick={() => setTheme(value)}
                className={styles.themeChoice}
              >
                <span className={styles.themePreview} data-preview={value}>
                  <span />
                  <span>
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
                <span>
                  <Icon size={14} />
                  {t(
                    value === "light"
                      ? "Light"
                      : value === "dark"
                        ? "Dark"
                        : "System",
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </SettingsSection>
      <SettingsSection title={t("Language and region")}>
        <SettingsRow
          label={t("Display language")}
          description={t("Used for navigation, dates, and controls.")}
        >
          <Select
            label={t("Display language")}
            placeholder={t("Language")}
            value={i18n.resolvedLanguage ?? "en"}
            onValueChange={(value) => void i18n.changeLanguage(value)}
            options={[
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
            ]}
          />
        </SettingsRow>
      </SettingsSection>
      <p className={styles.preferenceNote}>
        {t("Preferences are saved automatically in this browser.")}
      </p>
    </div>
  );
}
