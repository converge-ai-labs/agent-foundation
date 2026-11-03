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

it("validates localized catalogs with locale-specific plurals and interpolation", () => {
  expect(catalogErrors({ en, "zh-CN": zhCN })).toEqual([]);
  for (const key of Object.keys(en)) expect(i18n.exists(key), key).toBe(true);
});

// Guard the localized workflows; application-wide source linting
// and the remaining workbench workflows are tracked separately in #1248.
const surfaces = [
  "app.tsx",
  "conversations/navigation.tsx",
  "conversations/recent-navigation.tsx",
  "conversations/draft-navigation.tsx",
  "conversations/chat-search.tsx",
  "conversations/archived.tsx",
  "conversations/thread-row.tsx",
  "conversations/coordinator-entry.tsx",
  "conversations/delete-thread.tsx",
  "conversations/project-order.ts",
  "conversations/transcript.tsx",
  "conversations/message-text.tsx",
  "conversations/markdown-block.tsx",
  "conversations/copy-message.tsx",
  "conversations/context-activity.tsx",
  "conversations/failure-notice.tsx",
  "conversations/run-activity.tsx",
  "conversations/execution-details.tsx",
  "conversations/live-connection.tsx",
  "shell/image-preview.tsx",
  "shell/sidebar.tsx",
  "shell/workbench.tsx",
  "shell/ui.tsx",
  "sharing.tsx",
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
  "conversations/composer.tsx",
  "conversations/composer-editor.tsx",
  "conversations/composer-attachments.ts",
  "conversations/composer-messages.ts",
  "conversations/task-options.tsx",
  "conversations/clear-context.tsx",
  "shell/confirm-action.tsx",
  "shell/image-preview.tsx",
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

it("keeps retained composer feedback and terminal stop outcomes in both catalogs", () => {
  for (const key of [
    ...composerMessages,
    ...Object.values(stopOutcomeMessages),
  ]) {
    expect(Object.hasOwn(en, key), key).toBe(true);
    expect(Object.hasOwn(zhCN, key), key).toBe(true);
  }
});

it("covers literal feedback at its draft and composer sources, not only existing allowlist entries", () => {
  const missing: string[] = [];
  for (const file of [
    "composer.tsx",
    "draft.ts",
    "new-draft.ts",
    "new-conversation.tsx",
    "conversation.tsx",
  ]) {
    const source = readFileSync(
      new URL(`../conversations/${file}`, import.meta.url),
      "utf8",
    );
    const ast = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    // Inspect value branches only: condition literals and raw error expressions
    // are not application messages. Runtime recovery tests cover their wiring.
    function check(value: ts.Node) {
      if (
        ts.isStringLiteral(value) &&
        value.text &&
        !composerMessages.has(value.text)
      ) {
        missing.push(
          `${file}:${ast.getLineAndCharacterOfPosition(value.getStart()).line + 1}: ${value.text}`,
        );
      } else if (ts.isConditionalExpression(value)) {
        check(value.whenTrue);
        check(value.whenFalse);
      } else if (
        ts.isBinaryExpression(value) &&
        [
          ts.SyntaxKind.BarBarToken,
          ts.SyntaxKind.QuestionQuestionToken,
        ].includes(value.operatorToken.kind)
      ) {
        check(value.left);
        check(value.right);
      } else if (ts.isParenthesizedExpression(value)) {
        check(value.expression);
      }
    }
    function visit(node: ts.Node) {
      if (
        ts.isBinaryExpression(node) &&
        node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
        ts.isPropertyAccessExpression(node.left) &&
        node.left.name.text === "error"
      )
        check(node.right);
      if (
        ts.isPropertyAssignment(node) &&
        ts.isIdentifier(node.name) &&
        node.name.text === "message"
      )
        check(node.initializer);
      if (
        ts.isJsxAttribute(node) &&
        node.name.getText() === "unavailableReason" &&
        node.initializer &&
        ts.isJsxExpression(node.initializer) &&
        node.initializer.expression
      )
        check(node.initializer.expression);
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  expect(missing).toEqual([]);
});
