import { expect, it } from "vitest";
import { webCredentialState } from "./credential-state";

it.each([
  ["optional", true, "configured"],
  ["optional", false, "not_required"],
  ["required", true, "configured"],
  ["required", false, "not_configured"],
  ["forbidden", false, "not_required"],
] as const)(
  "reports %s credential with stored=%s as %s",
  (mode, stored, expected) => {
    expect(
      webCredentialState(
        { authentication: { mode }, configuration_schema: {} },
        { config: {}, credential_configured: stored },
      ),
    ).toBe(expected);
  },
);
