import { describe, expect, test } from "vitest";
import { charsOf, highlight } from "../src/scene/code";

// runs of one tone, trimmed: what each word or symbol is colored as
const spans = (line: string) => {
  const tones = highlight(line);
  const runs: [string, string][] = [];
  [...line].forEach((ch, i) => {
    const last = runs.at(-1);
    if (last && last[1] === tones[i]) last[0] += ch;
    else runs.push([ch, tones[i]]);
  });
  return runs
    .map(([text, tone]) => [text.trim(), tone])
    .filter(([text]) => text);
};

describe("highlight", () => {
  test("colors keywords, calls, names, and strings; punctuation steps back", () => {
    expect(spans('await agent.start("Review PR #482", key)')).toEqual([
      ["await", "kw"],
      ["agent", ""],
      [".", "p"],
      ["start", "fn"],
      ["(", "p"],
      ['"Review PR #482"', "str"],
      [",", "p"],
      ["key", ""],
      [")", "p"],
    ]);
    expect(spans("agent := client.Agent(marketBrief)")).toContainEqual([
      "Agent",
      "fn",
    ]);
    expect(spans("let agent = client.agent(DATA_ON_CALL);")).toContainEqual([
      "DATA_ON_CALL",
      "name",
    ]);
  });

  test("reads curl flags and file bodies", () => {
    expect(spans('curl -X POST "$A13N_URL/api/v1/threads" \\')).toEqual([
      ["curl", "fn"],
      ["-X", "kw"],
      ["POST", "name"],
      ['"$A13N_URL/api/v1/threads"', "str"],
      ["\\", "p"],
    ]);
    expect(spans("  -d @watch-v2.4.json")).toContainEqual([
      "@watch-v2.4.json",
      "str",
    ]);
  });
});

describe("charsOf", () => {
  test("marks what the call asks for and keeps spaces hard", () => {
    const [line] = charsOf(['start(⟨"Hi"⟩)']);
    expect(line.map((c) => c.ch).join("")).toBe('start("Hi")');
    expect(
      line
        .filter((c) => c.key)
        .map((c) => c.ch)
        .join(""),
    ).toBe('"Hi"');
    expect(line.find((c) => c.ch === '"')?.tone).toBe("str");
    expect(
      charsOf(["\ta b"])[0]
        .map((c) => c.ch)
        .join(""),
    ).toBe("\u00a0".repeat(4) + "a\u00a0b");
  });
});
