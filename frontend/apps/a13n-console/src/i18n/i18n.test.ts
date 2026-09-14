import { afterEach, expect, test } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { i18n } from "./index";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.")
      ? [path]
      : [];
  });
}

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

test("literal translation keys exist in the catalogs", () => {
  const keys = new Set<string>();
  for (const path of sourceFiles(
    fileURLToPath(new URL("..", import.meta.url)),
  )) {
    const source = readFileSync(path, "utf8");
    for (const match of source.matchAll(/\bt\(\s*"((?:\\.|[^"\\])*)"/g))
      keys.add(JSON.parse(`"${match[1]}"`) as string);
  }
  expect([...keys].filter((key) => !(key in en)).sort()).toEqual([]);
});
