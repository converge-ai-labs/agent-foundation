import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import ts from "typescript";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { i18n } from "./index";
import type { Schema } from "../transport/client";
import {
  composerMessages,
  stopOutcomeMessages,
} from "../conversations/composer-messages";

function catalogErrors(catalogs: Record<string, Record<string, string>>) {
  const errors: string[] = [];
  const logical = (key: string) =>
    key.replace(/_(zero|one|two|few|many|other)$/, "");
  const placeholders = (value: string) =>
    [...value.matchAll(/{{\s*([^},]+)(?:,[^}]+)?}}/g)]
      .map((m) => m[1].trim())
      .sort();
  const source = catalogs.en;
  for (const [locale, catalog] of Object.entries(catalogs)) {
    const categories = new Intl.PluralRules(locale).resolvedOptions()
      .pluralCategories;
    for (const key of new Set(Object.keys(source).map(logical))) {
      const plural = `${key}_other` in source;
      for (const suffix of plural ? categories.map((c) => `_${c}`) : [""]) {
        const entry = key + suffix;
        const value = catalog[entry];
        const reference = source[plural ? `${key}_other` : key];
        if (!value?.trim()) errors.push(`${locale}: missing or empty ${entry}`);
        else if (
          JSON.stringify(placeholders(value)) !==
          JSON.stringify(placeholders(reference))
        )
          errors.push(`${locale}: placeholders differ for ${entry}`);
      }
    }
    for (const key of Object.keys(catalog))
      if (
        !Object.keys(source).some(
          (sourceKey) => logical(sourceKey) === logical(key),
        )
      )
        errors.push(`${locale}: unknown ${key}`);
  }
  return errors;
}

it("validates onboarding catalogs with locale-specific plurals and interpolation", () => {
  expect(catalogErrors({ en, "zh-CN": zhCN })).toEqual([]);
  for (const key of Object.keys(en)) expect(i18n.exists(key), key).toBe(true);
});
it("rejects empty, missing and incompatible translations and missing English plurals", () => {
  const source = {
    Greeting: "Hello {{name}}",
    item_one: "{{count}} item",
    item_other: "{{count}} items",
  };
  expect(
    catalogErrors({
      en: source,
      "zh-CN": { Greeting: "你好 {{name}}", item_other: "{{count}} 项" },
    }),
  ).toEqual([]);
  expect(
    catalogErrors({
      en: source,
      "zh-CN": { Greeting: "", item_other: "{{total}} 项" },
    }),
  ).toEqual(
    expect.arrayContaining([
      "zh-CN: missing or empty Greeting",
      "zh-CN: placeholders differ for item_other",
    ]),
  );
  expect(
    catalogErrors({ en: { item_other: "{{count}} items" }, "zh-CN": {} }),
  ).toEqual(
    expect.arrayContaining([
      "en: missing or empty item_one",
      "zh-CN: missing or empty item_other",
    ]),
  );
});
it("formats counts and resolves known login states without translating provider identifiers", async () => {
  for (const locale of ["en", "zh-CN"]) {
    await i18n.changeLanguage(locale);
    const loginStates: Record<
      NonNullable<Schema<"LoginStatus">["state"]>,
      true
    > = {
      starting: true,
      waiting: true,
      succeeded: true,
      failed: true,
      cancelled: true,
      expired: true,
    };
    for (const state of Object.keys(loginStates))
      expect(i18n.exists(`login.${state}`)).toBe(true);
    expect(
      i18n.t("Account group summary", { count: 1, strategy: "weighted" }),
    ).toBe(locale === "en" ? "1 account · weighted" : "1 个账号 · weighted");
    expect(
      i18n.t("Account group summary", { count: 2, strategy: "weighted" }),
    ).toBe(locale === "en" ? "2 accounts · weighted" : "2 个账号 · weighted");
    expect(i18n.t("{{count, number}} context", { count: 12345 })).toContain(
      new Intl.NumberFormat(locale).format(12345),
    );
  }
  await i18n.changeLanguage("en");
});

// Guard the localized workflows; application-wide source linting
// and the remaining workbench workflows are tracked separately in #1248.
const surfaces = [
  "app.tsx",
  "shell/startup.tsx",
  "setup/wizard.tsx",
  "setup/setup.tsx",
  "setup/shell-review.tsx",
  "setup/live-opt-in.tsx",
  "configuration/model-editor.tsx",
  "configuration/resource-choice.tsx",
  "setup/accounts.tsx",
  "setup/account-messages.ts",
  "conversations/composer.tsx",
  "conversations/composer-editor.tsx",
  "conversations/composer-attachments.ts",
  "conversations/composer-messages.ts",
  "conversations/task-options.tsx",
  "conversations/clear-context.tsx",
  "shell/confirm-action.tsx",
  "shell/image-preview.tsx",
  "shell/ui.tsx",
];
it("keeps literal translation calls in the localized workflow catalogs", () => {
  const missing: string[] = [];
  for (const path of surfaces) {
    const source = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
    const ast = ts.createSourceFile(
      path,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    function visit(node: ts.Node) {
      if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === "t" &&
        node.arguments[0] &&
        ts.isStringLiteral(node.arguments[0])
      ) {
        const key = node.arguments[0].text;
        if (!(key in en) && !(`${key}_other` in en))
          missing.push(
            `${path}:${ast.getLineAndCharacterOfPosition(node.pos).line + 1}: ${key}`,
          );
      }
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  expect(missing).toEqual([]);
});

it("keeps retained composer feedback and terminal stop outcomes in both catalogs", () => {
  for (const key of [
    ...composerMessages,
    ...Object.values(stopOutcomeMessages),
  ]) {
    expect(Object.hasOwn(en, key), key).toBe(true);
    expect(Object.hasOwn(zhCN, key), key).toBe(true);
  }
});
