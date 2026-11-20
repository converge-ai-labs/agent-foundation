import { createInstance, type i18n as I18n } from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import zhCN from "./locales/zh-CN.json";

import { readLanguage } from "./preferences";

export const i18n: I18n = createInstance();

await i18n.use(initReactI18next).init({
  lng: readLanguage(),
  fallbackLng: "en",
  keySeparator: false,
  supportedLngs: ["en", "zh-CN", "hi"],
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
    hi: { translation: hi },
  },
  interpolation: { escapeValue: false },
});
