import type { RehypeCodeOptions } from "fumadocs-core/mdx-plugins";

type ShikiTransformer = NonNullable<RehypeCodeOptions["transformers"]>[number];

/*
 * Code blocks use one vivid palette with a distinct hue per token role, so
 * keywords, calls, types, keys, strings, and literals read apart at a glance.
 */
const scopes = {
  comment: ["comment", "punctuation.definition.comment"],
  keyword: [
    "keyword",
    "storage.type",
    "storage.modifier",
    "keyword.control",
    "keyword.other",
  ],
  call: [
    "entity.name.function",
    "support.function",
    "meta.function-call.generic",
    "entity.name.command",
  ],
  type: [
    "entity.name.type",
    "entity.name.class",
    "support.class",
    "support.type",
    "entity.other.inherited-class",
  ],
  key: [
    "support.type.property-name",
    "punctuation.support.type.property-name",
    "entity.name.tag",
  ],
  string: ["string", "punctuation.definition.string"],
  literal: [
    "constant",
    "constant.numeric",
    "constant.language",
    "constant.other",
    "constant.character",
    "variable.language",
    "support.constant",
    "constant.other.option",
  ],
  punctuation: ["punctuation", "keyword.operator", "meta.brace"],
  // Shell arguments, assignments, and parameters stay in text color.
  text: [
    "string.unquoted.argument",
    "variable.parameter",
    "variable.other",
    "meta.argument",
  ],
};

type Palette = Record<keyof typeof scopes, string>;

function codeTheme(name: string, type: "light" | "dark", palette: Palette) {
  return {
    name,
    type,
    colors: { "editor.foreground": palette.text },
    tokenColors: [
      { settings: { foreground: palette.text } },
      ...Object.entries(scopes).map(([role, scope]) => ({
        scope,
        settings: { foreground: palette[role as keyof Palette] },
      })),
    ],
  };
}

export const codeThemes = {
  light: codeTheme("a13n-light", "light", {
    text: "#1f2328",
    comment: "#8b8fa3",
    punctuation: "#6b7080",
    keyword: "#d6246e",
    call: "#7c3aed",
    type: "#0d9488",
    key: "#e5484d",
    string: "#10994c",
    literal: "#e8590c",
  }),
  dark: codeTheme("a13n-dark", "dark", {
    text: "#e6e8ee",
    comment: "#6f7488",
    punctuation: "#9aa0ad",
    keyword: "#ff5fa2",
    call: "#b98cff",
    type: "#2dd4bf",
    key: "#ff7a7f",
    string: "#4ade80",
    literal: "#ff9f43",
  }),
};

// Labels for untitled fences; plain text and output keep a bare frame.
const languageTitles: Record<string, string> = {
  bash: "Terminal",
  console: "Terminal",
  sh: "Terminal",
  shell: "Terminal",
  zsh: "Terminal",
  powershell: "PowerShell",
  python: "Python",
  yaml: "YAML",
  json: "JSON",
  toml: "TOML",
  markdown: "Markdown",
  typescript: "TypeScript",
};

/** Titles an untitled code block by its language, so every block names what it holds. */
export const transformerLanguageTitle: ShikiTransformer = {
  name: "a13n:language-title",
  pre(pre) {
    const title = languageTitles[this.options.lang];
    if (title && !pre.properties.title) pre.properties.title = title;
  },
};
