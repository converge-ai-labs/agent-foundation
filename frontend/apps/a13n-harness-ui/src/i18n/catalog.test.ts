import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import ts from "typescript";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { i18n } from "./index";

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

it("validates localized catalogs with locale-specific plurals and interpolation", () => {
  expect(catalogErrors({ en, "zh-CN": zhCN })).toEqual([]);
  for (const key of Object.keys(en)) expect(i18n.exists(key), key).toBe(true);
});

// Guard the workflows introduced in this slice; application-wide source linting
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
  "browser/panel.tsx",
  "browser/desktop.tsx",
  "browser/activity.tsx",
  "browser/setup.tsx",
  "browser/requirements.tsx",
  "browser/macos-setup.tsx",
  "browser/annotation-editor.tsx",
  "browser/annotation-text.tsx",
  "browser/text-input.ts",
  "configuration/browser-fields.tsx",
  "configuration/fields.tsx",
  "native/workspace.tsx",
  "conversations/composer-settings.tsx",
  "conversations/thread-run-choices.tsx",
  "conversations/model-controls.tsx",
  "conversations/model-picker.tsx",
  "conversations/thinking-picker.tsx",
  "conversations/fast-picker.tsx",
  "conversations/reasoning-mode-picker.tsx",
  "conversations/model-control-messages.ts",
];
it("keeps literal translation calls in the catalogs", () => {
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
