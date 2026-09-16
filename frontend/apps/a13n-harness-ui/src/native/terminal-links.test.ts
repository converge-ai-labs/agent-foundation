import { expect, it } from "vitest";
import { terminalFileLinks } from "./terminal-links";

it("links explicit absolute locations without guessing relative paths or URLs", () => {
  const text = "at /repo/main.py:42:3 and C:\\repo\\test.ts:8";
  expect(terminalFileLinks(text)).toEqual([
    { path: "/repo/main.py", line: 42, text: "/repo/main.py:42:3", start: 3 },
    {
      path: "C:\\repo\\test.ts",
      line: 8,
      text: "C:\\repo\\test.ts:8",
      start: text.indexOf("C:"),
    },
  ]);
  expect(
    terminalFileLinks(
      "src/file.py:2 https://example.com/file:4 ./file:3 /bad:0 /bad:9007199254740993",
    ),
  ).toEqual([]);
});
