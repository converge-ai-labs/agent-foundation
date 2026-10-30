import { afterEach, expect, test, vi } from "vitest";
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
  expect(i18n.t("Session")).toBe(en.Session);
  await i18n.changeLanguage("zh-CN");
  expect(i18n.t("Session")).toBe(zhCN.Session);
});

test("falls back to English for unsupported languages", async () => {
  await i18n.changeLanguage("fr");
  expect(i18n.t("Session")).toBe(en.Session);
});

test("programmatic language changes do not persist a manual choice", async () => {
  const setItem = vi.fn();
  vi.stubGlobal("localStorage", { setItem });
  try {
    await i18n.changeLanguage("zh-CN");
    expect(setItem).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
  }
});

test("translates catalogue text and shows new English text until translated", async () => {
  const groupLabel = "Files";
  const description = "Read, write, and organize files.";
  const newDescription = "Inspect a newly installed catalogue tool.";
  for (const language of ["en", "zh-CN"]) {
    await i18n.changeLanguage(language);
    expect(i18n.t(groupLabel)).toBe(language === "zh-CN" ? "文件" : groupLabel);
    expect(i18n.t(description)).toBe(
      language === "zh-CN" ? "读取、写入和整理文件。" : description,
    );
    expect(i18n.t(newDescription)).toBe(newDescription);
  }
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

test("distinguishes Session, Thread and Run in Chinese product labels", async () => {
  await i18n.changeLanguage("zh-CN");
  expect(i18n.t("Session")).toBe("会话");
  expect(i18n.t("Thread")).toBe("线程");
  expect(i18n.t("Main thread")).toBe("主线程");
  expect(i18n.t("Switch thread")).toBe("切换线程");
  expect(i18n.t("Run")).toBe("运行");
  expect(i18n.t("{{count}} runs", { count: 2 })).toBe("2 次运行");
});

test("English state and trigger labels consistently start with a capital letter", () => {
  const labels = Object.entries(en).filter(([key]) =>
    /^(state|trigger)\./.test(key),
  );
  expect(labels.length).toBeGreaterThan(0);
  for (const [key, label] of labels) expect(label, key).toMatch(/^[A-Z]/);
});

test("keeps inline phrases and protocol identifiers intact while normalizing peer labels", () => {
  expect(en["wrote the reply"]).toBe("wrote the reply");
  expect(en["and {{count}} more"]).toBe("and {{count}} more");
  expect(en["Api Key"]).toBe("API key");
  expect(en["Api Version"]).toBe("API version");
  const signature =
    "Verify X-A13n-Webhook-Signature with this secret and deduplicate by X-A13n-Delivery-Id.";
  expect(en[signature]).toBe(signature);
});
