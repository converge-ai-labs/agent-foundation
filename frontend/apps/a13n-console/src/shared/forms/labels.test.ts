import i18next from "i18next";
import { expect, it } from "vitest";
import { fieldLabel, translatedFieldLabel } from "./labels";

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
  expect(fieldLabel("Aws Session Token")).toBe("AWS session token");
});

it("prefers the sentence case form over a catalog entry that repeats the title", async () => {
  const i18n = i18next.createInstance();
  await i18n.init({
    lng: "en",
    resources: {
      en: { translation: { "Api Version": "Api Version" } },
      zh: { translation: { "Api Version": "API 版本" } },
    },
  });
  expect(translatedFieldLabel(i18n.t, "Api Version")).toBe("API version");
  await i18n.changeLanguage("zh");
  expect(translatedFieldLabel(i18n.t, "Api Version")).toBe("API 版本");
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
