import { expect, it } from "vitest";
import { i18n } from "../../i18n";
import { JsonDraftSyntaxError, parseJsonDraft } from "./json-draft";

it.each([
  [
    '{\n  "agent_id": "",\n}',
    "Expected a property name in double quotes.",
    3,
    1,
  ],
  [
    '{\r\n  "agent_id" ""\r\n}',
    "Expected a colon after the property name.",
    2,
    14,
  ],
  ['{"a": 1 "b": 2}', "Expected a comma between items.", 1, 9],
  ['{"a": "unfinished', "Missing closing double quote.", 1, 7],
  ["{/* comment */}", "JSON does not allow comments.", 1, 2],
  ["{", "Expected a closing brace.", 1, 2],
])(
  "reports a stable reason and position for %s",
  (text, reason, line, column) => {
    let error: unknown;
    try {
      parseJsonDraft(text);
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(JsonDraftSyntaxError);
    expect(error).toMatchObject({ message: reason, line, column });
    expect(i18n.t(reason, { lng: "en" })).toBe(reason);
    expect(i18n.t(reason, { lng: "zh-CN" })).not.toBe(reason);
  },
);

it("localizes positions and accepts strict JSON unchanged", () => {
  expect(
    i18n.t("Line {{line}}, column {{column}}", {
      lng: "zh-CN",
      line: 2,
      column: 18,
    }),
  ).toBe("第 2 行，第 18 列");
  const text = '{"agent_id":"","payload":{"content":[]},"extra":null}';
  expect(parseJsonDraft(text)).toEqual(JSON.parse(text));
});
