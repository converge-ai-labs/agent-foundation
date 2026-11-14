import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { I18nextProvider, useTranslation } from "react-i18next";
import { ChoiceField } from "a13n-ui";
import { SettingsChoice } from "../shell/settings";
import styles from "./language.module.css";
import { i18n } from "./index";
import {
  readLanguagePreference,
  resolveLanguage,
  saveLanguage,
  type LanguagePreference,
} from "./preferences";

const PreferenceContext = createContext<{
  preference: LanguagePreference;
  select: (value: LanguagePreference) => void;
} | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState(readLanguagePreference);
  const select = (value: LanguagePreference) => {
    setPreference(value);
    saveLanguage(value);
    void i18n.changeLanguage(resolveLanguage(value));
  };
  useEffect(() => {
    const followBrowser = () => {
      void i18n.changeLanguage(resolveLanguage(preference));
    };
    followBrowser();
    window.addEventListener("languagechange", followBrowser);
    return () => window.removeEventListener("languagechange", followBrowser);
  }, [preference]);
  return (
    <I18nextProvider i18n={i18n}>
      <PreferenceContext.Provider value={{ preference, select }}>
        <DocumentLanguage />
        {children}
      </PreferenceContext.Provider>
    </I18nextProvider>
  );
}

function DocumentLanguage() {
  const { i18n } = useTranslation();
  const language = i18n.resolvedLanguage ?? "en";
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  return null;
}

export function LanguageChoice({
  presentation = "field",
}: {
  presentation?: "field" | "row" | "compact";
}) {
  const { t } = useTranslation();
  const context = useContext(PreferenceContext);
  if (!context) throw new Error("LanguageChoice requires LanguageProvider");
  const { preference, select } = context;
  const Choice = presentation === "row" ? SettingsChoice : ChoiceField;
  return (
    <div
      className={
        presentation === "row"
          ? styles.row
          : presentation === "compact"
            ? styles.compact
            : styles.choice
      }
    >
      <Choice
        label={t("Display language")}
        hideLabel={presentation === "compact"}
        value={preference}
        onValueChange={(value) => {
          if (value !== "system" && value !== "en" && value !== "zh-CN") return;
          select(value);
        }}
        options={[
          { value: "system", label: t("System") },
          { value: "en", label: "English" },
          { value: "zh-CN", label: "简体中文" },
        ]}
      />
    </div>
  );
}
