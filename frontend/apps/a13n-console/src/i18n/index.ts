import { createInstance } from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

import { readLanguage, saveLanguage } from "./preferences";

export const i18n = createInstance();

await i18n.use(initReactI18next).init({
  lng: readLanguage(),
  fallbackLng: "en",
  keySeparator: false,
  supportedLngs: ["en", "zh-CN"],
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
  },
  interpolation: { escapeValue: false },
});

i18n.on("languageChanged", saveLanguage);
