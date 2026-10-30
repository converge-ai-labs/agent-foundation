import { expect, it } from "vitest";
import {
  connectionReviewTarget,
  requiresReviewer,
  validateReviewer,
} from "./reviewer";
import type { Schema } from "../../shared/api";

it("keeps absent reviewers optional and validates review bounds and selectors", () => {
  expect(requiresReviewer({}, [])).toBe(false);
  expect(
    requiresReviewer(
      { shell: { enabled: false, tools: { exec: { permission: "review" } } } },
      [],
    ),
  ).toBe(false);
  expect(
    requiresReviewer(
      { shell: { tools: { exec: { enabled: false, permission: "review" } } } },
      [],
    ),
  ).toBe(false);
  expect(() => validateReviewer({ model: "review-model" })).not.toThrow();
  for (const timeout_seconds of [0, -1, 121])
    expect(() =>
      validateReviewer({ model: "review-model", timeout_seconds }),
    ).toThrow();
  for (const selector of ["", " shell.exec", "shell.*.exec", "mcp/s*"])
    expect(() =>
      validateReviewer({ model: "review-model", rules: { [selector]: {} } }),
    ).toThrow();
  expect(() =>
    validateReviewer({
      model: "review-model",
      rules: { "shell.*": {}, "mcp/source/*": {}, "*": {}, "shell.exec": {} },
    }),
  ).not.toThrow();
});

it("uses execution identities for MCP and connector tools, including escaped names", () => {
  const connection = {
    id: "con_test",
    config: { url: "https://example.test/mcp" },
  } as Schema["Connection"];
  expect(connectionReviewTarget(connection, "read/a*b's!").selector).toBe(
    "mcp/con_test/read%2Fa%2Ab%27s%21",
  );
  expect(
    connectionReviewTarget(
      { ...connection, config: { app: "mail" } } as Schema["Connection"],
      "send",
    ).selector,
  ).toBe("tool/con_test/send");
});
