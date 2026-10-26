/** Lowercase a title for use inside a sentence while keeping acronyms such as "MCP". */
export function inlineLabel(label: string) {
  return label
    .split(" ")
    .map((word) => (/^[A-Z0-9]{2,}s?$/.test(word) ? word : word.toLowerCase()))
    .join(" ");
}
