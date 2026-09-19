import { expect, it } from "vitest";
import { credentialDescription, credentialHint } from "./credential-hint";
import { splitConfigurationSchema } from "./schemas";

const modal = {
  type: "object",
  properties: {
    workspace: { type: "string", title: "Workspace" },
    app_name: { type: "string", title: "App Name" },
    environment_name: {
      type: "string",
      title: "Environment Name",
      default: "main",
    },
  },
  required: ["workspace", "app_name"],
};

it("keeps required configuration inline and defers the rest", () => {
  const { primary, advanced } = splitConfigurationSchema(modal);
  expect(Object.keys(primary.properties)).toEqual(["workspace", "app_name"]);
  expect(primary.required).toEqual(["workspace", "app_name"]);
  expect(Object.keys(advanced.properties)).toEqual(["environment_name"]);
  expect(advanced.required).toEqual([]);
});

it("treats a schema without properties as empty on both sides", () => {
  const { primary, advanced } = splitConfigurationSchema({ type: "object" });
  expect(primary.properties).toEqual({});
  expect(advanced.properties).toEqual({});
  expect(splitConfigurationSchema(null).advanced.properties).toEqual({});
});

it("names the secret a service asks for", () => {
  expect(credentialHint({ properties: {} })).toBe("No credentials");
  expect(credentialHint(null)).toBe("No credentials");
  expect(credentialHint({ properties: { api_key: {} } })).toBe("API key");
  expect(credentialHint({ properties: { token: {} } })).toBe("Token");
  expect(
    credentialHint({ properties: { token_id: {}, token_secret: {} } }),
  ).toBe("Credentials");
  expect(credentialHint({ properties: { project: {} } })).toBe("Credentials");
});

it("describes where the secret comes from", () => {
  expect(credentialDescription({ properties: {} })).toBe(
    "This service needs no credentials.",
  );
  expect(credentialDescription({ properties: { api_key: {} } })).toBe(
    "Paste the API key from your {{provider}} account.",
  );
  expect(credentialDescription({ properties: { token: {} } })).toBe(
    "Paste the access token for {{provider}}.",
  );
  expect(
    credentialDescription({ properties: { token_id: {}, token_secret: {} } }),
  ).toBe("Enter the token ID and secret from your {{provider}} account.");
});
