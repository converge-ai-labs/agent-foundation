import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Select } from "a13n-ui";
import styles from "./app.module.css";
export function App() {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    document.documentElement.lang = i18n.resolvedLanguage ?? "en";
  }, [i18n.resolvedLanguage]);
  return (
    <main className={`a13n-root ${styles.main}`}>
      <h1>{t("welcome")}</h1>
      <Select
        label={t("language")}
        placeholder={t("language")}
        value={i18n.resolvedLanguage}
        onValueChange={(language) => void i18n.changeLanguage(language)}
        options={[
          { value: "en", label: "English" },
          { value: "zh-CN", label: "简体中文" },
        ]}
      />
    </main>
  );
}
