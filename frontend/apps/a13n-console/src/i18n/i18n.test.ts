import { afterEach, expect, test } from "vitest";
import { i18n } from "./index";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

test("starts in English and supports Simplified Chinese", async () => {
  expect(i18n.language).toBe("en");
  expect(i18n.t("welcome")).toBe(en.welcome);
  await i18n.changeLanguage("zh-CN");
  expect(i18n.t("welcome")).toBe(zhCN.welcome);
});

test("falls back to English for unsupported languages", async () => {
  await i18n.changeLanguage("fr");
  expect(i18n.t("welcome")).toBe(en.welcome);
});

test("translations have matching keys", () => {
  expect(Object.keys(zhCN).sort()).toEqual(Object.keys(en).sort());
});
