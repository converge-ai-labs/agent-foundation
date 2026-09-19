import { expect, it } from "vitest";
import { fieldLabel } from "./labels";

it("writes schema titles in sentence case", () => {
  expect(fieldLabel("Sandbox Domain")).toBe("Sandbox domain");
  expect(fieldLabel("App Name")).toBe("App name");
  expect(fieldLabel("Allow Plaintext Private Link")).toBe(
    "Allow plaintext private link",
  );
});

it("restores acronyms the title case flattened", () => {
  expect(fieldLabel("Api Key")).toBe("API key");
  expect(fieldLabel("Base Url")).toBe("Base URL");
  expect(fieldLabel("Token Id")).toBe("Token ID");
  expect(fieldLabel("Url Prefix")).toBe("URL prefix");
});

it("keeps tokens the schema spells itself", () => {
  expect(fieldLabel("E2B Template")).toBe("E2B template");
  expect(fieldLabel("OAuth Client")).toBe("OAuth client");
  expect(fieldLabel("API URL")).toBe("API URL");
  expect(fieldLabel("Organization ID")).toBe("Organization ID");
});

it("leaves translated labels alone", () => {
  expect(fieldLabel("API 密钥")).toBe("API 密钥");
  expect(fieldLabel("沙箱域名")).toBe("沙箱域名");
});
