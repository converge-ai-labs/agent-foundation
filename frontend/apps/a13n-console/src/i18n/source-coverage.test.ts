import { readdirSync, readFileSync } from "node:fs";
import ts from "typescript";
import { expect, it } from "vitest";
import { metrics } from "../features/monitoring/labels";
import { providerCategories } from "../features/providers/categories";
import { navigationGroups } from "../layout/navigation";
import en from "./locales/en.json";

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
const displayAttributes = new Set([
  "aria-label",
  "closeLabel",
  "description",
  "label",
  "placeholder",
  "text",
  "title",
]);

type ConstantResolver = (
  node: ts.Identifier,
) => ts.Expression | null | undefined;

function bindingNames(name: ts.BindingName): string[] {
  if (ts.isIdentifier(name)) return [name.text];
  return name.elements.flatMap((element) =>
    ts.isBindingElement(element) ? bindingNames(element.name) : [],
  );
}

/** Resolve local constants without crossing a shadowing lexical scope. */
function localConstants(ast: ts.SourceFile): ConstantResolver {
  const scopes = new Map<ts.Node, Map<string, ts.Expression | null>>();
  function declare(scope: ts.Node, name: string, value: ts.Expression | null) {
    let bindings = scopes.get(scope);
    if (!bindings) scopes.set(scope, (bindings = new Map()));
    bindings.set(name, bindings.has(name) ? null : value);
  }
  function collect(node: ts.Node) {
    if (ts.isVariableDeclaration(node)) {
      const container = node.parent.parent;
      const scope = ts.isVariableStatement(container)
        ? container.parent
        : container;
      const constant =
        ts.isIdentifier(node.name) &&
        node.initializer &&
        node.parent.flags & ts.NodeFlags.Const
          ? node.initializer
          : null;
      for (const name of bindingNames(node.name))
        declare(scope, name, constant);
    }
    if (ts.isParameter(node))
      for (const name of bindingNames(node.name))
        declare(node.parent, name, null);
    ts.forEachChild(node, collect);
  }
  collect(ast);
  return (node) => {
    for (let scope = node.parent; scope; scope = scope.parent) {
      const bindings = scopes.get(scope);
      if (bindings?.has(node.text)) return bindings.get(node.text);
    }
  };
}

// Exact, file-scoped exceptions for language names, brands, commands, paths,
// and diagnostics. Do not add directories or broad substring matches here.
const untranslatedExceptions: Record<string, string[]> = {
  "auth/context.tsx": ["Missing client provider", "Missing identity provider"],
  "features/agents/web-tool-settings.tsx": ["example.com, docs.example.org"],
  "features/conversations/composer/attach-dialog.tsx": ["https://"],
  "features/conversations/session-editor.tsx": ["a13n-bot"],
  "features/credentials/editor.tsx": ["api.github.com"],
  "features/credentials/session-policy-fields.tsx": ["GH_TOKEN"],
  "features/environments/kubernetes-configuration.tsx": ["ConfigMap", "Secret"],
  "features/environments/kubernetes-details.tsx": ["Envd"],
  "features/environments/kubernetes-editors.tsx": ["ConfigMap", "Secret"],
  "features/environments/provider-configuration.tsx": ["auto", "base"],
  "features/mcp/create.tsx": ["https://example.com/mcp"],
  "features/memories/detail/file-dialogs.tsx": ["notes/topic.md"],
  "features/memories/fields.tsx": ["README.md"],
  "features/models/provider-authorization.tsx": [
    "http://127.0.0.1:1456/auth/callback?code=…&state=…",
  ],
  "features/models/provider-connection.tsx": [
    "https://api.example.com/v1",
    "x-conversation-id",
  ],
  "features/monitoring/panel-editor.tsx": ["PromQL"],
  "features/monitoring/sources.tsx": ["HTTP/protobuf", "gRPC"],
  "features/settings/preferences.tsx": ["English", "简体中文"],
  "features/settings/webhooks/detail.tsx": ["HMAC-SHA256"],
  "features/settings/webhooks/fields.tsx": [
    "https://your-app.example.com/hooks/a13n",
    "sch_…",
    "sess_…",
    "thread_…",
  ],
  "features/skills/detail/index.tsx": ["GitHub"],
  "features/skills/import-dialog.tsx": [
    "GitHub",
    "https://github.com/owner/repository",
  ],
  "features/skills/list.tsx": ["GitHub"],
  "features/skills/source.tsx": ["GitHub"],
  "features/traces/content.tsx": ["null"],
  "layout/appearance.tsx": ["AppearanceProvider is required."],
  "layout/workspace.tsx": [
    "Missing access provider",
    "Missing workspace provider",
  ],
  "shared/forms/environment-variables.tsx": ["APP_MODE"],
  "shared/forms/header-fields.tsx": ["X-API-Key"],
};

// These application components own translation for the listed props. Callers
// supply catalog keys, while shared a13n-ui primitives still require t().
const internallyTranslatedAttributes: Record<string, Set<string>> = {
  ConnectionTest: new Set(["description"]),
  ProviderGroup: new Set(["title"]),
  ProviderKeyLink: new Set(["label"]),
};

// Raw source viewers display commands and serialized values verbatim.
const preservedAttributes: Record<string, Set<string>> = {
  CodeText: new Set(["language", "text"]),
};

function attributeOwner(node: ts.JsxAttribute, ast: ts.SourceFile): string {
  const opening = node.parent.parent;
  return ts.isJsxOpeningElement(opening) || ts.isJsxSelfClosingElement(opening)
    ? opening.tagName.getText(ast)
    : "";
}

function untranslatedJSX(
  path: string,
  source: string,
  exceptions: string[] = untranslatedExceptions[path] ?? [],
): string[] {
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
      !exceptions.includes(text)
    ) {
      const position = ast.getLineAndCharacterOfPosition(node.getStart());
      errors.push(
        `${path}:${position.line + 1}:${position.character + 1}: ${text}`,
      );
    }
  }

  // Imported and runtime values stay opaque so user content and external
  // diagnostics are never treated as repository-owned copy.
  const resolveConstant = localConstants(ast);

  const resolving = new Set<ts.Expression>();
  function expression(node: ts.Expression) {
    if (ts.isIdentifier(node)) {
      const value = resolveConstant(node);
      if (value && !resolving.has(value)) {
        resolving.add(value);
        expression(value);
        resolving.delete(value);
      }
    } else if (
      ts.isElementAccessExpression(node) &&
      ts.isIdentifier(node.expression)
    ) {
      const value = resolveConstant(node.expression);
      if (value && ts.isObjectLiteralExpression(value)) {
        for (const property of value.properties) {
          if (ts.isPropertyAssignment(property))
            expression(property.initializer);
        }
      }
    } else if (ts.isTemplateExpression(node)) {
      check(
        node.head.text +
          node.templateSpans.map((span) => span.literal.text).join(" "),
        node,
      );
    } else if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node)
    ) {
      check(node.text, node);
    } else if (ts.isConditionalExpression(node)) {
      expression(node.whenTrue);
      expression(node.whenFalse);
    } else if (
      ts.isBinaryExpression(node) &&
      [
        ts.SyntaxKind.AmpersandAmpersandToken,
        ts.SyntaxKind.BarBarToken,
        ts.SyntaxKind.QuestionQuestionToken,
      ].includes(node.operatorToken.kind)
    ) {
      expression(node.right);
    } else if (ts.isParenthesizedExpression(node)) {
      expression(node.expression);
    }
  }

  function choiceOptions(node: ts.Expression) {
    if (ts.isIdentifier(node)) {
      const value = resolveConstant(node);
      if (value && ts.isArrayLiteralExpression(value)) choiceOptions(value);
    } else if (ts.isArrayLiteralExpression(node)) {
      for (const option of node.elements) {
        if (!ts.isObjectLiteralExpression(option)) continue;
        for (const property of option.properties) {
          if (
            ts.isPropertyAssignment(property) &&
            property.name.getText(ast) === "label"
          )
            expression(property.initializer);
        }
      }
    }
  }

  function notificationProperties(object: ts.ObjectLiteralExpression) {
    for (const property of object.properties) {
      if (!ts.isPropertyAssignment(property)) continue;
      const name = property.name.getText(ast).replace(/^['"]|['"]$/g, "");
      if (["aria-label", "children", "description", "title"].includes(name))
        expression(property.initializer);
      if (
        name === "actionProps" &&
        ts.isObjectLiteralExpression(property.initializer)
      )
        notificationProperties(property.initializer);
    }
  }

  function preservedCode(node: ts.Node): boolean {
    for (let current = node.parent; current; current = current.parent) {
      if (
        (ts.isJsxElement(current) &&
          ["code", "pre"].includes(
            current.openingElement.tagName.getText(ast),
          )) ||
        (ts.isJsxSelfClosingElement(current) &&
          ["code", "pre"].includes(current.tagName.getText(ast)))
      )
        return true;
      if (ts.isSourceFile(current)) return false;
    }
    return false;
  }

  function visit(node: ts.Node) {
    if (
      path.endsWith(".tsx") &&
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "Error" &&
      node.arguments?.[0]
    )
      expression(node.arguments[0]);
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "add" &&
      /(?:toast|manager)$/.test(node.expression.expression.getText(ast)) &&
      node.arguments[0] &&
      ts.isObjectLiteralExpression(node.arguments[0])
    )
      notificationProperties(node.arguments[0]);
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
    if (!preservedCode(node)) {
      if (ts.isJsxText(node)) check(node.text, node);
      if (ts.isJsxAttribute(node)) {
        const name = node.name.getText(ast);
        const owner = attributeOwner(node, ast);
        if (preservedAttributes[owner]?.has(name)) return;
        if (internallyTranslatedAttributes[owner]?.has(name)) return;
        if (
          displayAttributes.has(name) &&
          node.initializer &&
          ts.isStringLiteral(node.initializer)
        )
          check(node.initializer.text, node);
      }
      if (
        ts.isJsxExpression(node) &&
        node.expression &&
        (!ts.isJsxAttribute(node.parent) ||
          (displayAttributes.has(node.parent.name.getText(ast)) &&
            !internallyTranslatedAttributes[
              attributeOwner(node.parent, ast)
            ]?.has(node.parent.name.getText(ast)) &&
            !preservedAttributes[attributeOwner(node.parent, ast)]?.has(
              node.parent.name.getText(ast),
            )))
      )
        expression(node.expression);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return errors;
}

function translationKeyLiterals(
  node: ts.Expression,
  resolveConstant?: ConstantResolver,
  resolving = new Set<ts.Expression>(),
): string[] {
  function resolve(expression: ts.Expression | null | undefined): string[] {
    if (!expression || resolving.has(expression)) return [];
    resolving.add(expression);
    const keys = translationKeyLiterals(expression, resolveConstant, resolving);
    resolving.delete(expression);
    return keys;
  }
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    return [node.text];
  if (ts.isIdentifier(node)) return resolve(resolveConstant?.(node));
  if (ts.isElementAccessExpression(node) && ts.isIdentifier(node.expression))
    return resolve(resolveConstant?.(node.expression));
  if (ts.isObjectLiteralExpression(node))
    return node.properties.flatMap((property) =>
      ts.isPropertyAssignment(property) ? resolve(property.initializer) : [],
    );
  if (ts.isConditionalExpression(node))
    return [...resolve(node.whenTrue), ...resolve(node.whenFalse)];
  if (ts.isBinaryExpression(node)) {
    if (node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken)
      return resolve(node.right);
    if (
      [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(
        node.operatorToken.kind,
      )
    )
      return [...resolve(node.left), ...resolve(node.right)];
  }
  if (
    ts.isParenthesizedExpression(node) ||
    ts.isAsExpression(node) ||
    ts.isTypeAssertionExpression(node) ||
    ts.isSatisfiesExpression(node)
  )
    return resolve(node.expression);
  return [];
}

function translationKeys(
  path: string,
  source: string,
): [string, ts.SourceFile, ts.Node][] {
  const ast = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    path.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const resolveConstant = localConstants(ast);
  const keys: [string, ts.SourceFile, ts.Node][] = [];
  function collect(expression: ts.Expression) {
    for (const key of translationKeyLiterals(expression, resolveConstant))
      keys.push([key, ast, expression]);
  }
  function visit(node: ts.Node) {
    if (
      ts.isCallExpression(node) &&
      ((ts.isIdentifier(node.expression) && node.expression.text === "t") ||
        (ts.isPropertyAccessExpression(node.expression) &&
          node.expression.name.text === "t")) &&
      node.arguments[0]
    )
      collect(node.arguments[0]);
    if (
      ts.isJsxAttribute(node) &&
      internallyTranslatedAttributes[attributeOwner(node, ast)]?.has(
        node.name.getText(ast),
      )
    ) {
      if (node.initializer && ts.isStringLiteral(node.initializer))
        collect(node.initializer);
      else if (
        node.initializer &&
        ts.isJsxExpression(node.initializer) &&
        node.initializer.expression
      )
        collect(node.initializer.expression);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return keys;
}

function propertyLiterals(path: string, names: Set<string>): string[] {
  const ast = ts.createSourceFile(
    path,
    readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const values: string[] = [];
  function visit(node: ts.Node) {
    if (
      ts.isPropertyAssignment(node) &&
      names.has(node.name.getText(ast).replace(/^['"]|['"]$/g, "")) &&
      (ts.isStringLiteral(node.initializer) ||
        ts.isNoSubstitutionTemplateLiteral(node.initializer))
    )
      values.push(node.initializer.text);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return values;
}

it("detects untranslated JSX, attributes, options, and notifications", () => {
  expect(
    surfaces.flatMap((path) =>
      untranslatedJSX(
        path,
        readFileSync(new URL(`../${path}`, import.meta.url), "utf8"),
      ),
    ),
  ).toEqual([]);
}, 30_000);

it("reports actionable locations for omissions and accepts translated wrappers", () => {
  const bad = untranslatedJSX(
    "fixture.tsx",
    `const label = "Save changes"; const options = [{value: "x", label: "Choose option"}]; <><Button>{label}</Button><ChoiceField options={options}/><input placeholder="Search here"/><span>{ready ? "Ready now" : t("Loading")}</span>{toast.add({title: "Task done", actionProps: {children: "Open chat"}})}</>`,
  );
  expect(bad).toHaveLength(6);
  expect(bad.every((entry) => /^fixture\.tsx:\d+:\d+: /.test(entry))).toBe(
    true,
  );

  const good = `const label = t("Save changes"); const options = [{value: "x", label: t("Choose option")}]; const labels = {ready: "Ready now"}; <><Button>{label}</Button><ChoiceField options={options}/><input placeholder={t("Search here")}/><span>{t(labels[state])}</span>{toast.add({title: t("Task done"), description: result.message, actionProps: {children: t("Open chat")}})}</>`;
  expect(untranslatedJSX("fixture.tsx", good)).toEqual([]);
});

it("accepts lower-level translation wrappers but checks ordinary props and errors", () => {
  const good = `<><ProviderGroup title="Providers"/><ProviderKeyLink label="Provider keys"/><ConnectionTest description="Test connection"/>{new Error(t("Invalid settings"))}{new Error(error.message)}</>`;
  expect(untranslatedJSX("fixture.tsx", good)).toEqual([]);

  const bad = untranslatedJSX(
    "fixture.tsx",
    `<><ProviderGroup title="Providers"/><Button title="Save changes"/>{new Error("Invalid settings")}{new Error(error.message)}</>`,
  );
  expect(bad).toHaveLength(2);
  expect(bad[0]).toContain("Save changes");
  expect(bad[1]).toContain("Invalid settings");
});

it("does not resolve same-named constants across function scopes", () => {
  const source = `const labels = {ready: "Ready now"}; function Fact({label}) { return <dt>{label}</dt>; } function LevelPill({level}) { const label = labels[level]; return <span>{label ? t(label) : level}</span>; }`;
  expect(untranslatedJSX("fixture.tsx", source)).toEqual([]);
  expect(translationKeys("fixture.tsx", source).map(([key]) => key)).toEqual([
    "Ready now",
  ]);
});

it("preserves user content, commands, paths, and narrow exceptions", () => {
  const source = `<><pre>npm run dev</pre><code>pnpm --dir frontend test</code><span>{message.content}</span><span>{diagnostic}</span><img src="/assets/logo.svg"/><span>Example only</span></>`;
  expect(untranslatedJSX("fixture.tsx", source, ["Example only"])).toEqual([]);
  expect(untranslatedJSX("fixture.tsx", "<p>Example only</p>")).toHaveLength(1);
});

it("keeps literal translation calls in the catalogs", () => {
  const missing = surfaces.flatMap((path) => {
    const source = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
    return translationKeys(path, source).flatMap(([key, ast, node]) => {
      if (key in en || `${key}_other` in en) return [];
      const position = ast.getLineAndCharacterOfPosition(node.getStart());
      return [`${path}:${position.line + 1}:${position.character + 1}: ${key}`];
    });
  });
  expect(missing).toEqual([]);
});

it("keeps static navigation and dynamic application keys in the catalogs", () => {
  const navigationKeys = navigationGroups.flatMap((group) => [
    group.label,
    ...group.entries.flatMap(([, label, , children]) => [
      label,
      ...(children ?? []).map(([, childLabel]) => childLabel),
    ]),
  ]);
  const settingsKeys = propertyLiterals(
    "features/settings/navigation.ts",
    new Set(["label", "title", "description"]),
  );
  const applicationKeys = [
    ...navigationKeys,
    ...settingsKeys,
    ...providerCategories.flatMap(
      ({ label, description, emptyTitle, empty }) => [
        label,
        description,
        emptyTitle,
        empty,
      ],
    ),
    ...metrics.map((metric) => `metric.${metric}`),
  ].filter(Boolean);
  expect(applicationKeys.length).toBeGreaterThan(0);
  expect(applicationKeys.filter((key) => !(key in en))).toEqual([]);

  const traceKeys = translationKeys(
    "features/traces/values.tsx",
    readFileSync(
      new URL("../features/traces/values.tsx", import.meta.url),
      "utf8",
    ),
  ).map(([key]) => key);
  expect(traceKeys).toEqual(
    expect.arrayContaining([
      "Trace level",
      "Debug",
      "Info",
      "Notice",
      "Warning",
      "Error",
      "Critical",
      "Fatal",
    ]),
  );
});

it.each([
  ['({ ready: "Missing asserted key" } as const)', "fixture.tsx"],
  [
    '({ ready: "Missing asserted key" } satisfies Record<string, string>)',
    "fixture.tsx",
  ],
  [
    '({ ready: "Missing asserted key" } as Record<string, string>)',
    "fixture.tsx",
  ],
  ['(<Record<string, string>>{ ready: "Missing asserted key" })', "fixture.ts"],
])("validates indirect translation keys in %s", (initializer, path) => {
  const missing = "Missing asserted key";
  const keys = translationKeys(
    path,
    `const labels = ${initializer};
      const label = labels[state];
      t(label);
      t(labels[choices[0]]);
      choices.map((value) => ({ label: t(labels[value]) }));`,
  ).map(([key]) => key);
  expect(keys).toEqual([missing, missing, missing]);
  expect([...new Set(keys.filter((key) => !(key in en)))]).toEqual([missing]);
});

it("validates literal key fixtures, including conditional branches", () => {
  expect(
    translationKeys(
      "fixture.tsx",
      `t('Known'); t(condition ? "Also known" : \`Third known\`); t(dynamicKey); <ProviderGroup title="Providers"/>;`,
    ).map(([key]) => key),
  ).toEqual(["Known", "Also known", "Third known", "Providers"]);
});
