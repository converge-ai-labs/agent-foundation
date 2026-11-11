import { expect, it } from "vitest";
import { sessionConfiguration } from "../../shared/forms/validation";
import {
  credentialExample,
  sessionPolicyError,
  type SessionPolicy,
} from "./session-policy";

const reference = {
  env: "GH_TOKEN",
  credential_id: "ecred_0123456789abcdef0123",
  inject_hosts: ["api.github.com"],
};
const policy: SessionPolicy = {
  egress_mode: "controlled",
  egress: {
    destinations: { mode: "allowlist", hosts: ["api.github.com"] },
    secrets: [reference],
  },
};

it("requires injection hosts to be allowed and variables to be unique", () => {
  expect(sessionPolicyError(policy)).toBeUndefined();
  expect(
    sessionPolicyError({
      ...policy,
      egress: {
        destinations: { mode: "allowlist", hosts: [] },
        secrets: [reference],
      },
    }),
  ).toMatch(/every injection domain/);
  expect(
    sessionPolicyError({
      ...policy,
      egress: {
        destinations: { mode: "public" },
        secrets: [reference, reference],
      },
    }),
  ).toMatch(/different variable name/);
  expect(sessionPolicyError({ ...policy, egress_mode: "inherit" })).toMatch(
    /require controlled/,
  );
});

it("does not produce executable examples from invalid variables or injected URL syntax", () => {
  expect(credentialExample(reference)).toBe(
    'curl -H "Authorization: Bearer ${GH_TOKEN}" https://api.github.com/',
  );
  expect(credentialExample({ ...reference, env: "$(id)" })).toBeUndefined();
  expect(
    credentialExample({ ...reference, inject_hosts: ["api.github.com/$(id)"] }),
  ).toBeUndefined();
  expect(
    credentialExample({ ...reference, env: "SSL_CERT_FILE" }),
  ).toBeUndefined();
  expect(
    credentialExample({ ...reference, inject_hosts: ["127.0.0.1"] }),
  ).toBeUndefined();
});

it("uses the Service schema for advanced edits and rejects inline secret values", () => {
  expect(
    sessionConfiguration(
      JSON.stringify({ ...policy, working_directory: "/workspace" }),
    ),
  ).toMatchObject(policy);
  expect(() =>
    sessionConfiguration(
      JSON.stringify({
        egress: {
          destinations: { mode: "public" },
          secrets: [{ ...reference, value: "raw-token" }],
        },
      }),
    ),
  ).toThrow();
  expect(() => sessionConfiguration('{"working_directory":123}')).toThrow();
});
