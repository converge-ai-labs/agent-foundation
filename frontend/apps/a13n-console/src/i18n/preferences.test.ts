import { afterEach, expect, it, vi } from "vitest";
import { readLanguage, saveLanguage } from "./preferences";
afterEach(() => vi.unstubAllGlobals());
it("restores supported languages and defaults to English", () => {
  const getItem = vi.fn().mockReturnValue("zh-CN");
  const setItem = vi.fn();
  vi.stubGlobal("localStorage", { getItem, setItem });
  expect(readLanguage()).toBe("zh-CN");
  getItem.mockReturnValue("fr");
  expect(readLanguage()).toBe("en");
  saveLanguage("zh-CN");
  expect(setItem).toHaveBeenCalledWith("a13n-console-language", "zh-CN");
});
it("keeps working when storage is unavailable", () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  });
  expect(readLanguage()).toBe("en");
  expect(() => saveLanguage("zh-CN")).not.toThrow();
});
