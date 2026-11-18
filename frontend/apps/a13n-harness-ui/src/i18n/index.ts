import { createInstance } from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import zhCN from "./locales/zh-CN.json";
import { readLanguage } from "./preferences";
export { useTranslation } from "react-i18next";

export const i18n = createInstance();
void i18n.use(initReactI18next).init({
  initImmediate: false,
  lng: readLanguage(),
  fallbackLng: "en",
  supportedLngs: ["en", "zh-CN", "hi"],
  keySeparator: false,
  nsSeparator: false,
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
    hi: { translation: hi },
  },
  interpolation: { escapeValue: false },
});
