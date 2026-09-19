/**
 * Schema titles arrive as Title Case of the property key ("Api Key", "Base
 * Url"). The console writes field labels in sentence case, so a label keeps
 * only the capitals that belong to a word: an acronym, or a token the schema
 * already spells itself ("E2B", "OAuth").
 */
const acronyms = [
  "API",
  "URL",
  "ID",
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
