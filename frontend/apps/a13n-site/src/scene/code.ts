/** A little syntax color: strings, keywords, calls and names; punctuation steps back. */
export type Tone = "" | "str" | "kw" | "fn" | "name" | "p";

const SYNTAX: [Tone, RegExp][] = [
  ["str", /"[^"]*"|@\S+/y],
  ["kw", /\b(?:await|const|let)\b|-[XHd]\b/y],
  ["fn", /\w+(?=\()|curl\b/y],
  ["name", /[A-Z]\w*/y],
  ["", /[\w$]+|\s+/y],
  ["p", /./y],
];

/** The tone of each character of a line of code. */
export function highlight(line: string): Tone[] {
  const tones: Tone[] = [];
  for (let i = 0; i < line.length;) {
    for (const [tone, re] of SYNTAX) {
      re.lastIndex = i;
      const match = re.exec(line);
      if (!match) continue;
      tones.push(...Array<Tone>(match[0].length).fill(tone));
      i += match[0].length;
      break;
    }
  }
  return tones;
}

/** One character of code; key marks what the call asks for. */
export interface Char {
  ch: string;
  key: boolean;
  tone: Tone;
}

export const BLANK: Char = { ch: "\u00a0", key: false, tone: "" };

/**
 * Lines of code as characters, with ⟨⟩ around what the call asks for.
 * Spaces are hard, so a lone space in a cell keeps its width.
 */
export function charsOf(lines: string[]): Char[][] {
  return lines.map((line) => {
    const text = line.replaceAll("\t", "    ");
    const tones = highlight(text.replace(/[⟨⟩]/g, ""));
    const chars: Char[] = [];
    let key = false;
    for (const ch of text) {
      if (ch === "⟨" || ch === "⟩") key = ch === "⟨";
      else
        chars.push({
          ch: ch === " " ? BLANK.ch : ch,
          key,
          tone: tones[chars.length],
        });
    }
    return chars;
  });
}
