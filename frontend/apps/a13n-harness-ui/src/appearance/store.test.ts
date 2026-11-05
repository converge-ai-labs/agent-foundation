// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { AppearanceStore, APPEARANCE_KEY, LEGACY_THEME_KEY } from "./store";
import { defaultAppearance } from "./model";
let stop: (() => void) | undefined;
afterEach(() => {
  stop?.();
  stop = undefined;
  localStorage.clear();
  vi.restoreAllMocks();
  document.documentElement.removeAttribute("style");
  document.documentElement.classList.remove("dark");
});
it("migrates the legacy theme once and resets only appearance", () => {
  localStorage.setItem(LEGACY_THEME_KEY, "dark");
  localStorage.setItem("a13n-harness-ui.language", "zh-CN");
  const store = new AppearanceStore(window);
  stop = store.start();
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem(LEGACY_THEME_KEY)).toBeNull();
  expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toMatchObject({
    version: 1,
    theme: "dark",
  });
  store.setSize("code", 18);
  expect(store.getSnapshot().appearance.sizes.composer).toBe(16);
  expect(
    document.documentElement.style.getPropertyValue("--harness-code-size"),
  ).toBe("18px");
  store.reset();
  expect(new AppearanceStore(window).getSnapshot().appearance).toEqual(
    defaultAppearance(),
  );
  expect(localStorage.getItem("a13n-harness-ui.language")).toBe("zh-CN");
});
it("does not overwrite unknown versions or resurrect a legacy theme", () => {
  const raw = '{"version":2,"theme":"dark"}';
  localStorage.setItem(APPEARANCE_KEY, raw);
  localStorage.setItem(LEGACY_THEME_KEY, "dark");
  const store = new AppearanceStore(window);
  stop = store.start();
  expect(store.getSnapshot().appearance).toEqual(defaultAppearance());
  expect(localStorage.getItem(APPEARANCE_KEY)).toBe(raw);
});
it("keeps memory changes and legacy data after failed writes, then recovers", () => {
  localStorage.setItem(LEGACY_THEME_KEY, "dark");
  const write = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new Error("quota");
    });
  const store = new AppearanceStore(window);
  stop = store.start();
  store.setSize("conversation", 20);
  expect(store.getSnapshot()).toMatchObject({
    saveFailed: true,
    appearance: { theme: "dark", sizes: { conversation: 20 } },
  });
  expect(
    document.documentElement.style.getPropertyValue("--harness-prose-size"),
  ).toBe("20px");
  expect(localStorage.getItem(LEGACY_THEME_KEY)).toBe("dark");
  write.mockRestore();
  store.setSize("navigation", 16);
  expect(store.getSnapshot().saveFailed).toBe(false);
  expect(localStorage.getItem(LEGACY_THEME_KEY)).toBeNull();
});
it("works when reading storage is unavailable", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  const store = new AppearanceStore(window);
  expect(store.getSnapshot().appearance).toEqual(defaultAppearance());
});
it("applies remote snapshots and deletion without write-back", () => {
  const store = new AppearanceStore(window);
  stop = store.start();
  const write = vi.spyOn(Storage.prototype, "setItem");
  const remote = {
    ...defaultAppearance(),
    theme: "dark",
    sizes: { ...defaultAppearance().sizes, terminal: 20 },
  };
  window.dispatchEvent(
    new StorageEvent("storage", {
      key: APPEARANCE_KEY,
      newValue: JSON.stringify(remote),
      storageArea: localStorage,
    }),
  );
  expect(store.getSnapshot()).toMatchObject({ dark: true, appearance: remote });
  window.dispatchEvent(
    new StorageEvent("storage", { key: APPEARANCE_KEY, newValue: null }),
  );
  expect(store.getSnapshot().appearance).toEqual(defaultAppearance());
  expect(write).not.toHaveBeenCalled();
});
it("listens to system theme only while selected and cleans up", () => {
  let dark = true;
  const callbacks = new Set<() => void>();
  vi.stubGlobal("matchMedia", () => ({
    matches: dark,
    addEventListener: (_: string, fn: () => void) => callbacks.add(fn),
    removeEventListener: (_: string, fn: () => void) => callbacks.delete(fn),
  }));
  const store = new AppearanceStore(window);
  stop = store.start();
  expect(callbacks.size).toBe(0);
  store.setTheme("system");
  expect(callbacks.size).toBe(1);
  expect(store.getSnapshot().dark).toBe(true);
  dark = false;
  callbacks.forEach((fn) => fn());
  expect(store.getSnapshot().dark).toBe(false);
  store.setTheme("dark");
  expect(callbacks.size).toBe(0);
  expect(store.getSnapshot().dark).toBe(true);
  store.setTheme("system");
  stop();
  expect(callbacks.size).toBe(0);
  vi.unstubAllGlobals();
});
