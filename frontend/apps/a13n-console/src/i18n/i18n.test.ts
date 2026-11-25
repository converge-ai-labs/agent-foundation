import { afterEach, expect, test, vi } from "vitest";
import { i18n } from "./index";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import zhCN from "./locales/zh-CN.json";

type Catalog = Record<string, string>;

const pluralSuffix = /_(zero|one|two|few|many|other)$/;

function logicalKey(key: string): string {
  return key.replace(pluralSuffix, "");
}

function placeholders(value: string): string[] {
  return [
    ...new Set(
      [...value.matchAll(/{{\s*([^},]+)(?:,[^}]+)?}}/g)].map((match) =>
        match[1].trim(),
      ),
    ),
  ].sort();
}

function catalogErrors(catalogs: Record<string, Catalog>): string[] {
  const errors: string[] = [];
  const source = catalogs.en;
  const logicalKeys = new Set(Object.keys(source).map(logicalKey));
  const pluralKeys = new Set(
    Object.keys(source)
      .filter((key) => pluralSuffix.test(key))
      .map(logicalKey),
  );

  for (const [locale, catalog] of Object.entries(catalogs)) {
    const categories = new Intl.PluralRules(locale).resolvedOptions()
      .pluralCategories;
    for (const key of logicalKeys) {
      const plural = pluralKeys.has(key);
      const reference = source[plural ? `${key}_other` : key] ?? "";
      for (const suffix of plural ? categories.map((c) => `_${c}`) : [""]) {
        const entry = key + suffix;
        const value = catalog[entry];
        if (!value?.trim()) {
          errors.push(`${locale}: missing or empty ${entry}`);
        } else if (
          reference.trim() &&
          JSON.stringify(placeholders(value)) !==
            JSON.stringify(placeholders(reference))
        ) {
          errors.push(`${locale}: placeholders differ for ${entry}`);
        }
      }
    }
    for (const key of Object.keys(catalog)) {
      if (!logicalKeys.has(logicalKey(key))) {
        errors.push(`${locale}: unknown ${key}`);
      }
    }
  }
  return errors;
}

const validCatalogs = {
  en: {
    "Welcome {{name}}": "Welcome {{name}}",
    "{{count}} item_one": "{{count}} item",
    "{{count}} item_other": "{{count}} items",
  },
  "zh-CN": {
    "Welcome {{name}}": "欢迎，{{name}}",
    "{{count}} item_other": "{{count}} 个项目",
  },
};

afterEach(async () => {
  await i18n.changeLanguage("en");
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

test("localized catalogs have required, nonempty, compatible messages", () => {
  expect(catalogErrors({ en, "zh-CN": zhCN, hi })).toEqual([]);
});

test("catalog validation accepts locale-specific plural suffixes", () => {
  expect(catalogErrors(validCatalogs)).toEqual([]);
});

test("catalog validation reports missing, empty, and unknown messages", () => {
  const catalogs = {
    en: { ...validCatalogs.en, "{{count}} item_other": "  " },
    "zh-CN": {
      "{{count}} item_other": "{{count}} 个项目",
      Unexpected: "意外消息",
    },
  };
  expect(catalogErrors(catalogs)).toEqual([
    "en: missing or empty {{count}} item_other",
    "zh-CN: missing or empty Welcome {{name}}",
    "zh-CN: unknown Unexpected",
  ]);
});

test("catalog validation reports incompatible placeholders", () => {
  const catalogs = {
    en: validCatalogs.en,
    "zh-CN": {
      ...validCatalogs["zh-CN"],
      "Welcome {{name}}": "欢迎，{{username}}",
    },
  };
  expect(catalogErrors(catalogs)).toEqual([
    "zh-CN: placeholders differ for Welcome {{name}}",
  ]);
});

test("catalog validation reports each locale's required plural forms", () => {
  const english = Object.fromEntries(
    Object.entries(validCatalogs.en).filter(
      ([key]) => key !== "{{count}} item_one",
    ),
  );
  expect(
    catalogErrors({ en: english, "zh-CN": validCatalogs["zh-CN"] }),
  ).toEqual(["en: missing or empty {{count}} item_one"]);
});
