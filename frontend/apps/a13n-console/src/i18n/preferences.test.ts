import { afterEach, expect, it, vi } from "vitest";
import {
  readLanguage,
  readLanguagePreference,
  resolveLanguage,
  saveLanguage,
} from "./preferences";
afterEach(() => vi.unstubAllGlobals());

it("follows the browser when no language was selected", () => {
  vi.stubGlobal("localStorage", { getItem: () => null });
  vi.stubGlobal("navigator", { languages: ["fr-FR", "zh-CN", "en-US"] });
  expect(readLanguagePreference()).toBe("system");
  expect(readLanguage()).toBe("zh-CN");
  expect(resolveLanguage("system")).toBe("zh-CN");

  vi.stubGlobal("navigator", { languages: ["fr-FR"], language: "fr-FR" });
  expect(readLanguage()).toBe("en");
  vi.stubGlobal("navigator", { languages: [], language: "zh-SG" });
  expect(readLanguage()).toBe("zh-CN");
});

it("restores explicit choices and can return to browser language", () => {
  const getItem = vi.fn().mockReturnValue("en");
  const setItem = vi.fn();
  vi.stubGlobal("localStorage", { getItem, setItem });
  vi.stubGlobal("navigator", { languages: ["zh-CN"] });
  expect(readLanguagePreference()).toBe("en");
  expect(readLanguage()).toBe("en");
  getItem.mockReturnValue("zh-CN");
  expect(readLanguage()).toBe("zh-CN");
  getItem.mockReturnValue("fr");
  expect(readLanguagePreference()).toBe("system");
  expect(readLanguage()).toBe("zh-CN");
  saveLanguage("system");
  expect(setItem).toHaveBeenCalledWith("a13n-console-language", "system");
});

it("keeps working when storage is unavailable", () => {
  vi.stubGlobal("navigator", { languages: ["zh-CN"] });
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  });
  expect(readLanguage()).toBe("zh-CN");
  expect(() => saveLanguage("zh-CN")).not.toThrow();
});
