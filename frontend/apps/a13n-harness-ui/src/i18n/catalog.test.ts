import { expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { i18n } from "./index";
import type { Schema } from "../transport/client";
import { resourceKinds } from "../configuration/documents";
import { inlineLabel } from "../shell/text";
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

// Scan all production TypeScript sources, rather than a curated surface list.
function sourceFiles(directory: URL, prefix = ""): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = `${prefix}${entry.name}`;
    if (entry.isDirectory())
      return sourceFiles(new URL(`${entry.name}/`, directory), `${path}/`);
    return /\.tsx?$/.test(path) && !/\.(test|spec|d)\.tsx?$/.test(path)
      ? [path]
      : [];
  });
}
const surfaces = sourceFiles(new URL("../", import.meta.url));
// Exceptions are protocol names, product brands, code languages, or example paths,
// scoped to their source file so unrelated application copy is still detected.
const untranslatedExceptions: Record<string, string[]> = {
  "app.tsx": ["Harness"],
  "configuration/appearance.tsx": [
    'const greeting = "Hello, 世界";',
    'echo "Hello, 世界"',
  ],
  "configuration/device-fields.tsx": ["/api/devices/", "/connect"],
  "configuration/envd.tsx": ["envd"],
  "configuration/envd-fields.tsx": ["envd"],
  "i18n/language.tsx": ["English"],
  "shell/debug.tsx": ["undefined"],
  "configuration/fields.tsx": ["Sidekick"],
  "configuration/project-folders.tsx": ["/path/to/project"],
  "conversations/composer.tsx": ["- HEAD"],
  "conversations/markdown-block.tsx": ["mermaid", "text"],
  "mcp-apps/app-card.tsx": ["- MCP App"],
  "setup/subscriptions.tsx": ["Codex", "Grok"],
  "shell/sidebar.tsx": ["Harness"],
  "shell/startup.tsx": ["Harness"],
  "shell/workbench.tsx": ["Harness UI -", "Harness", "Harness UI"],
  "usage/page.tsx": ["USD -"],
};
const displayAttributes = new Set([
  "title",
  "label",
  "description",
  "placeholder",
  "aria-label",
  "closeLabel",
  "text",
]);
function untranslatedJSX(path: string, source: string): string[] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const errors: string[] = [];
  function check(value: string, node: ts.Node) {
    const text = value.trim().replace(/\s+/g, " ");
    if (
      /[a-zA-Z]{3}/.test(text) &&
      !/^[A-Z]+$/.test(text) &&
      !untranslatedExceptions[path]?.includes(text)
    )
      errors.push(
        `${path}:${ast.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${text}`,
      );
  }
  // Resolve only unambiguous local constants; imported and runtime data remain opaque.
  const constants = new Map<string, ts.Expression | null>();
  function collect(node: ts.Node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isVariableDeclarationList(node.parent) &&
      node.parent.flags & ts.NodeFlags.Const
    ) {
      const name = node.name.text;
      constants.set(name, constants.has(name) ? null : node.initializer);
    }
    ts.forEachChild(node, collect);
  }
  collect(ast);
  const resolving = new Set<string>();
  function expression(node: ts.Expression) {
    if (ts.isIdentifier(node)) {
      const value = constants.get(node.text);
      if (value && !resolving.has(node.text)) {
        resolving.add(node.text);
        expression(value);
        resolving.delete(node.text);
      }
    } else if (
      ts.isElementAccessExpression(node) &&
      ts.isIdentifier(node.expression)
    ) {
      const value = constants.get(node.expression.text);
      if (value && ts.isObjectLiteralExpression(value))
        for (const property of value.properties)
          if (ts.isPropertyAssignment(property))
            expression(property.initializer);
    } else if (ts.isTemplateExpression(node)) {
      check(
        node.head.text +
          node.templateSpans.map((span) => span.literal.text).join(" "),
        node,
      );
    } else if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node)
    )
      check(node.text, node);
    else if (ts.isConditionalExpression(node)) {
      expression(node.whenTrue);
      expression(node.whenFalse);
    } else if (
      ts.isBinaryExpression(node) &&
      [
        ts.SyntaxKind.BarBarToken,
        ts.SyntaxKind.QuestionQuestionToken,
        ts.SyntaxKind.AmpersandAmpersandToken,
      ].includes(node.operatorToken.kind)
    )
      expression(node.right);
    else if (ts.isParenthesizedExpression(node)) expression(node.expression);
  }
  function choiceOptions(node: ts.Expression) {
    if (ts.isIdentifier(node)) {
      const value = constants.get(node.text);
      if (value && ts.isArrayLiteralExpression(value)) choiceOptions(value);
    } else if (ts.isArrayLiteralExpression(node)) {
      for (const option of node.elements) {
        if (!ts.isObjectLiteralExpression(option)) continue;
        for (const property of option.properties)
          if (
            ts.isPropertyAssignment(property) &&
            property.name.getText(ast) === "label"
          )
            expression(property.initializer);
      }
    }
  }
  function toastProperties(object: ts.ObjectLiteralExpression) {
    for (const property of object.properties) {
      if (!ts.isPropertyAssignment(property)) continue;
      const name = property.name.getText(ast).replace(/^['\"]|['\"]$/g, "");
      if (["title", "description", "children", "aria-label"].includes(name))
        expression(property.initializer);
      if (
        name === "actionProps" &&
        ts.isObjectLiteralExpression(property.initializer)
      )
        toastProperties(property.initializer);
    }
  }
  function visit(node: ts.Node) {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "add" &&
      /(?:toast|manager)$/.test(node.expression.expression.getText(ast)) &&
      node.arguments[0] &&
      ts.isObjectLiteralExpression(node.arguments[0])
    )
      toastProperties(node.arguments[0]);
    if (
      ts.isJsxAttribute(node) &&
      node.name.getText(ast) === "options" &&
      ts.isJsxAttributes(node.parent) &&
      (ts.isJsxOpeningElement(node.parent.parent) ||
        ts.isJsxSelfClosingElement(node.parent.parent)) &&
      node.parent.parent.tagName.getText(ast) === "ChoiceField" &&
      node.initializer &&
      ts.isJsxExpression(node.initializer) &&
      node.initializer.expression
    )
      choiceOptions(node.initializer.expression);
    if (ts.isJsxText(node)) check(node.text, node);
    if (
      ts.isJsxAttribute(node) &&
      displayAttributes.has(node.name.getText(ast)) &&
      node.initializer &&
      ts.isStringLiteral(node.initializer)
    )
      check(node.initializer.text, node);
    if (
      ts.isJsxExpression(node) &&
      node.expression &&
      (!ts.isJsxAttribute(node.parent) ||
        displayAttributes.has(node.parent.name.getText(ast)))
    )
      expression(node.expression);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return errors;
}
it("detects untranslated JSX text, attributes, and value branches across production sources", () => {
  expect(
    surfaces.flatMap((path) =>
      untranslatedJSX(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
});
it("detects untranslated display values without flagging translated or technical expressions", () => {
  expect(
    untranslatedJSX(
      "fixture.tsx",
      `<><p>Hello world</p><input placeholder="Search here"/><span>{ready ? "Ready now" : t("Loading")}</span><span>{name}</span>{toast.add({title: "Task done", actionProps: {children: "Open chat"}})}</>`,
    ),
  ).toHaveLength(5);
  expect(
    untranslatedJSX(
      "fixture.tsx",
      `<><p>{t("Hello world")}</p><input placeholder={t("Search here")}/><span>{provider.name}</span>{toast.add({title: t("Task done"), description: notice.brief, actionProps: {children: t("Open chat")}})}</>`,
    ),
  ).toEqual([]);
});

it("resolves local display constants, choice labels, maps and templates", () => {
  const bad = `const label = "Save changes"; const options = [{value: "x", label: "Choose option"}]; const labels = {x: "Waiting now"}; <><Button>{label}</Button><ChoiceField options={options}/><span>{labels[state]}</span><span>{\`Hello \${name}\`}</span><Notice text="Try again"/></>`;
  expect(untranslatedJSX("fixture.tsx", bad)).toHaveLength(5);
  const good = `const label = t("Save changes"); const options = [{value: "x", label: t("Choose option")}]; const labels = {x: "Waiting now"}; <><Button>{label}</Button><ChoiceField options={options}/><span>{t(labels[state])}</span><span>{provider.name}</span><Notice text={t("Try again")}/></>`;
  expect(untranslatedJSX("fixture.tsx", good)).toEqual([]);
});

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
    function checkKey(node: ts.Expression) {
      if (ts.isStringLiteral(node)) {
        const key = node.text;
        if (!(key in en) && !(`${key}_other` in en))
          missing.push(
            `${path}:${ast.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${key}`,
          );
      } else if (ts.isConditionalExpression(node)) {
        checkKey(node.whenTrue);
        checkKey(node.whenFalse);
      } else if (ts.isParenthesizedExpression(node)) {
        checkKey(node.expression);
      }
    }
    function visit(node: ts.Node) {
      if (
        ts.isCallExpression(node) &&
        ((ts.isIdentifier(node.expression) && node.expression.text === "t") ||
          (ts.isPropertyAccessExpression(node.expression) &&
            node.expression.name.text === "t")) &&
        node.arguments[0]
      )
        checkKey(node.arguments[0]);
      if (
        path === "voice-session.ts" &&
        ts.isNewExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === "VoiceMessage" &&
        node.arguments?.[0]
      )
        checkKey(node.arguments[0]);
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  expect(missing).toEqual([]);
});

// Expand template keys whose substitutions come from a local literal array's find().
// Other dynamic expressions need their own source-specific checks or runtime tests.
function missingFiniteTemplateKeys(path: string, source: string): string[] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const arrays = new Map<string, string[]>();
  const missing: string[] = [];
  function collect(node: ts.Node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isCallExpression(node.initializer) &&
      ts.isPropertyAccessExpression(node.initializer.expression) &&
      node.initializer.expression.name.text === "find" &&
      ts.isArrayLiteralExpression(node.initializer.expression.expression)
    ) {
      const elements = node.initializer.expression.expression.elements;
      if (elements.every(ts.isStringLiteral))
        arrays.set(
          node.name.text,
          elements.map((item) => (item as ts.StringLiteral).text),
        );
    }
    ts.forEachChild(node, collect);
  }
  collect(ast);
  function visit(node: ts.Node) {
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "t" &&
      node.arguments[0] &&
      ts.isTemplateExpression(node.arguments[0])
    ) {
      const template = node.arguments[0];
      let keys = [template.head.text];
      for (const span of template.templateSpans) {
        const values = ts.isIdentifier(span.expression)
          ? arrays.get(span.expression.text)
          : undefined;
        if (!values) {
          keys = [];
          break;
        }
        keys = keys.flatMap((key) =>
          values.map((value) => key + value + span.literal.text),
        );
      }
      for (const key of keys)
        if (!(key in en) && !(`${key}_other` in en))
          missing.push(`${path}: ${key}`);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return missing;
}
it("checks finite template translation keys from literal lookup arrays", () => {
  expect(
    surfaces.flatMap((path) =>
      missingFiniteTemplateKeys(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
  expect(
    missingFiniteTemplateKeys(
      "fixture.tsx",
      'const kind = ["threads", "mystery"].find(test); t(`Discovered ${kind}`);',
    ),
  ).toEqual(["fixture.tsx: Discovered mystery"]);
  expect(
    missingFiniteTemplateKeys(
      "fixture.tsx",
      'const kind = ["threads", "projects"].find(test); t(`Discovered ${kind}`); t(provider.value);',
    ),
  ).toEqual([]);
});

// Local error sources are translated at render time, rather than where created.
// Inspect only the named application-owned sources; external Error values stay raw.
function missingLocalErrorKeys(path: string, source: string): string[] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const missing: string[] = [];
  function check(node: ts.Expression) {
    if (ts.isStringLiteral(node)) {
      if (!(node.text in en) && !(`${node.text}_other` in en))
        missing.push(
          `${path}:${ast.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${node.text}`,
        );
    } else if (ts.isConditionalExpression(node)) {
      check(node.whenTrue);
      check(node.whenFalse);
    } else if (ts.isParenthesizedExpression(node)) check(node.expression);
  }
  function visit(node: ts.Node) {
    if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      [
        "PushError",
        "VoiceMessage",
        "ClipboardMessage",
        "CaptureMessage",
      ].includes(node.expression.text) &&
      node.arguments?.[0]
    )
      check(node.arguments[0]);
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "failLocal" &&
      node.arguments[0]
    )
      check(node.arguments[0]);
    if (
      path.endsWith("push.ts") &&
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "guidance" &&
      node.initializer
    )
      check(node.initializer);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return missing;
}
it("covers locally sourced retained errors and push guidance", () => {
  expect(
    surfaces.flatMap((path) =>
      missingLocalErrorKeys(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
  expect(
    missingLocalErrorKeys(
      "push.ts",
      `
    new PushError("Missing push error");
    new ClipboardMessage("Missing clipboard error");
    new CaptureMessage("Missing capture error");
    failLocal("Missing frame error");
    const guidance = condition ? "Settings" : "Missing guidance";
    new Error("External error stays raw");
  `,
    ),
  ).toEqual([
    "push.ts:2: Missing push error",
    "push.ts:3: Missing clipboard error",
    "push.ts:4: Missing capture error",
    "push.ts:5: Missing frame error",
    "push.ts:6: Missing guidance",
  ]);
  expect(
    missingLocalErrorKeys(
      "push.ts",
      `new PushError("Settings"); failLocal("Settings"); const guidance = condition ? "Settings" : "Settings"; new Error("External");`,
    ),
  ).toEqual([]);
});

// Check literal values retained in React state that is translated when rendered.
// Nonliteral values may be external and require source-specific coverage.
function missingTranslatedStateKeys(path: string, source: string): string[] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const setters = new Map<string, string>();
  const translated = new Set<string>();
  const missing: string[] = [];
  function collect(node: ts.Node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isArrayBindingPattern(node.name) &&
      node.name.elements.length >= 2 &&
      ts.isBindingElement(node.name.elements[0]) &&
      ts.isBindingElement(node.name.elements[1]) &&
      ts.isIdentifier(node.name.elements[0].name) &&
      ts.isIdentifier(node.name.elements[1].name) &&
      node.initializer &&
      ts.isCallExpression(node.initializer) &&
      ts.isIdentifier(node.initializer.expression) &&
      node.initializer.expression.text === "useState"
    )
      setters.set(
        node.name.elements[1].name.text,
        node.name.elements[0].name.text,
      );
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "t" &&
      node.arguments[0] &&
      ts.isIdentifier(node.arguments[0])
    )
      translated.add(node.arguments[0].text);
    ts.forEachChild(node, collect);
  }
  function check(node: ts.Expression) {
    if (ts.isStringLiteral(node) && node.text) {
      if (!(node.text in en) && !(`${node.text}_other` in en))
        missing.push(
          `${path}:${ast.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${node.text}`,
        );
    } else if (ts.isConditionalExpression(node)) {
      check(node.whenTrue);
      check(node.whenFalse);
    } else if (ts.isParenthesizedExpression(node)) check(node.expression);
  }
  function visit(node: ts.Node) {
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.arguments[0] &&
      translated.has(setters.get(node.expression.text) ?? "")
    )
      check(node.arguments[0]);
    ts.forEachChild(node, visit);
  }
  collect(ast);
  visit(ast);
  return missing;
}
it("checks literal state feedback translated at render time", () => {
  expect(
    surfaces.flatMap((path) =>
      missingTranslatedStateKeys(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
  expect(
    missingTranslatedStateKeys(
      "fixture.tsx",
      `const [message, setMessage] = useState(""); setMessage(flag ? "Settings" : "Missing state label"); t(message);`,
    ),
  ).toEqual(["fixture.tsx:1: Missing state label"]);
  expect(
    missingTranslatedStateKeys(
      "fixture.tsx",
      `const [message, setMessage] = useState(""); setMessage(external.message); t(message);`,
    ),
  ).toEqual([]);
});

// Check locally defined finite lookup tables passed to translation. Dynamic server
// values and generated labels have separate ownership and runtime coverage.
function missingLookupKeys(path: string, source: string): string[] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const tables = new Map<string, ts.ObjectLiteralExpression>();
  const used = new Set<string>();
  function visit(node: ts.Node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer
    ) {
      let value = node.initializer;
      while (
        ts.isAsExpression(value) ||
        ts.isSatisfiesExpression(value) ||
        ts.isParenthesizedExpression(value)
      )
        value = value.expression;
      if (ts.isObjectLiteralExpression(value))
        tables.set(node.name.text, value);
    }
    if (
      ts.isCallExpression(node) &&
      ((ts.isIdentifier(node.expression) && node.expression.text === "t") ||
        (ts.isPropertyAccessExpression(node.expression) &&
          node.expression.name.text === "t")) &&
      node.arguments[0] &&
      ts.isElementAccessExpression(node.arguments[0]) &&
      ts.isIdentifier(node.arguments[0].expression)
    )
      used.add(node.arguments[0].expression.text);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  const missing: string[] = [];
  for (const name of used) {
    const table = tables.get(name);
    if (!table) continue;
    for (const property of table.properties) {
      if (
        !ts.isPropertyAssignment(property) ||
        !ts.isStringLiteral(property.initializer)
      )
        continue;
      const key = property.initializer.text;
      if (!(key in en) && !(`${key}_other` in en))
        missing.push(
          `${path}:${ast.getLineAndCharacterOfPosition(property.initializer.getStart()).line + 1}: ${key}`,
        );
    }
  }
  return missing;
}
it("keeps finite translated lookup table values in the catalog", () => {
  expect(
    surfaces.flatMap((path) =>
      missingLookupKeys(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
  expect(
    missingLookupKeys(
      "fixture.tsx",
      `const labels = { ok: "Settings", bad: "Missing lookup label" } as const; t(labels[status]);`,
    ),
  ).toEqual(["fixture.tsx:1: Missing lookup label"]);
  expect(
    missingLookupKeys(
      "fixture.tsx",
      `const labels = { ok: "Settings" }; t(labels[status]); t(provider.label);`,
    ),
  ).toEqual([]);
});

it("keeps resource kind labels and their inline variants in both catalogs", () => {
  for (const { label } of resourceKinds) {
    for (const key of [label, inlineLabel(label)]) {
      expect(Object.hasOwn(en, key), key).toBe(true);
      expect(Object.hasOwn(zhCN, key), key).toBe(true);
    }
  }
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
