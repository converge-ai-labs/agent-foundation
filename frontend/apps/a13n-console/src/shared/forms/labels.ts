import type { TFunction } from "i18next";

/**
 * Schema titles arrive as Title Case of the property key ("Api Key", "Base
 * Url"). The console writes field labels in sentence case, so a label keeps
 * only the capitals that belong to a word: an acronym, or a token the schema
 * already spells itself ("E2B", "OAuth").
 */
const acronyms = [
  "API",
  "AWS",
  "URL",
  "URI",
  "ID",
  "IDs",
  "PIDs",
  "TTL",
  "ZDR",
  "JSON",
  "OAuth",
  "HTTP",
  "HTTPS",
  "MCP",
  "SSL",
  "TLS",
  "CPU",
  "GPU",
  "IP",
  "YAML",
  "TOML",
  "CSV",
  "UI",
  "SDK",
  "vCPUs",
];
const byLowercase = new Map(
  acronyms.map((acronym) => [acronym.toLocaleLowerCase(), acronym]),
);

/** A token the schema spells deliberately: a digit or more than one capital. */
function spelled(token: string) {
  return /\d/.test(token) || (token.match(/\p{Lu}/gu)?.length ?? 0) > 1;
}

export function fieldLabel(title: string) {
  return title
    .split(" ")
    .map((token, index) => {
      if (!token || spelled(token)) return token;
      const acronym = byLowercase.get(token.toLocaleLowerCase());
      if (acronym) return acronym;
      const lowered = token.toLocaleLowerCase();
      return index === 0
        ? lowered[0].toLocaleUpperCase() + lowered.slice(1)
        : lowered;
    })
    .join(" ");
}

/** A key with no schema title reads as words: "shell_profiles" → "Shell profiles". */
export function humanize(key: string) {
  const words = key.replaceAll(/[_-]+/g, " ").trim();
  return words ? words[0].toLocaleUpperCase() + words.slice(1) : key;
}

/**
 * Schema titles arrive in Title Case; the catalog keys their sentence-case
 * form. A "_field" context wins where a word means something else in a form,
 * such as Memory as RAM rather than agent memory.
 */
export function translatedFieldLabel(t: TFunction, title: string) {
  const english = fieldLabel(title);
  // A catalog entry that repeats the title is no translation; the sentence
  // case form reads better.
  const translated = t(title, { defaultValue: english });
  return t(english, {
    context: "field",
    defaultValue: translated === title ? english : translated,
  });
}
