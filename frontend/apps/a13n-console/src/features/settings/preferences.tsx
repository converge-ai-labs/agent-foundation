import { ChoiceField, cn, ToggleGroup, ToggleGroupItem } from "a13n-ui";

import { SettingsRow, SettingsSection } from "a13n-ui";
import { Laptop, Moon, Sun } from "lucide-react";

import { useTranslation } from "react-i18next";
import { useAppearance } from "../../layout/appearance";
import styles from "./settings.module.css";

export function Preferences() {
  const { t, i18n } = useTranslation(),
    { theme, setTheme } = useAppearance();
  return (
    <div className={styles.preferences}>
      <SettingsSection title={t("Appearance")}>
        <ToggleGroup
          className={cn(styles.themeOptions, "w-full")}
          value={[theme]}
          variant="default"
          onValueChange={(values) => {
            const next = values[0];
            if (next === "light" || next === "dark" || next === "system")
              setTheme(next);
          }}
          aria-label={t("Color theme")}
        >
          {(["light", "dark", "system"] as const).map((value) => {
            const Icon =
              value === "light" ? Sun : value === "dark" ? Moon : Laptop;
            return (
              <ToggleGroupItem
                type="button"
                key={value}
                value={value}
                className="h-auto min-w-0 flex-col gap-0 whitespace-normal p-1.5 sm:h-auto"
                variant="default"
              >
                <span
                  className={`${styles.themePreview} w-full shrink-0`}
                  data-preview={value}
                  aria-hidden="true"
                >
                  <span />
                  <span>
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
                <span className="flex items-center justify-center gap-2 pt-2 pb-1 text-xs">
                  <Icon className="size-3.5" />
                  {t(
                    value === "light"
                      ? "Light"
                      : value === "dark"
                        ? "Dark"
                        : "System",
                  )}
                </span>
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>
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
      <p className={styles.preferenceNote}>
        {t("Preferences are saved automatically in this browser.")}
      </p>
    </div>
  );
}
