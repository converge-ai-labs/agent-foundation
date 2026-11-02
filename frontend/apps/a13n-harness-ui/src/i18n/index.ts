import { createInstance } from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { readLanguage } from "./preferences";
export { useTranslation } from "react-i18next";

export const i18n = createInstance();
void i18n.use(initReactI18next).init({
  initImmediate: false,
  lng: readLanguage(),
  fallbackLng: "en",
  supportedLngs: ["en", "zh-CN"],
  keySeparator: false,
  nsSeparator: false,
  resources: { en: { translation: en }, "zh-CN": { translation: zhCN } },
  interpolation: { escapeValue: false },
});
