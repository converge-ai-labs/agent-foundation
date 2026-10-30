import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { i18n } from "../../i18n";
import { readLanguage } from "../../i18n/preferences";
import { LanguageSettings } from "./preferences";

const preferenceKey = "a13n-console-language";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

async function mountSettings() {
  const instance = i18n.cloneInstance();
  await instance.changeLanguage(readLanguage());
  const view = render(
    <I18nextProvider i18n={instance}>
      <LanguageSettings />
    </I18nextProvider>,
  );
  const user = userEvent.setup();
  return {
    ...view,
    async selectLanguage(name: string) {
      await user.click(screen.getByRole("combobox"));
      await user.click(await screen.findByRole("option", { name }));
    },
  };
}

it("updates visible text immediately when the language control changes", async () => {
  const { selectLanguage } = await mountSettings();
  expect(
    screen.getByRole("combobox", { name: "Display language" }),
  ).toBeTruthy();
  expect(
    screen.getByText("Preferences are saved automatically in this browser."),
  ).toBeTruthy();

  await selectLanguage("简体中文");
  expect(
    await screen.findByRole("combobox", { name: "显示语言" }),
  ).toBeTruthy();
  expect(screen.getByText("偏好设置会自动保存在此浏览器中。")).toBeTruthy();

  await selectLanguage("English");
  expect(
    await screen.findByRole("combobox", { name: "Display language" }),
  ).toBeTruthy();
  expect(
    screen.getByText("Preferences are saved automatically in this browser."),
  ).toBeTruthy();
});

it.each([
  ["English", "en", "Display language"],
  ["简体中文", "zh-CN", "显示语言"],
])("restores the explicit %s choice on remount", async (name, value, label) => {
  const view = await mountSettings();
  await view.selectLanguage(name);
  expect(localStorage.getItem(preferenceKey)).toBe(value);
  view.unmount();

  await mountSettings();
  expect(screen.getByRole("combobox", { name: label }).textContent).toBe(name);
});

it("returns to browser language without saving the resolved language as a manual choice", async () => {
  localStorage.setItem(preferenceKey, "en");
  const languages = vi
    .spyOn(navigator, "languages", "get")
    .mockReturnValue(["zh-CN"]);
  const view = await mountSettings();
  expect(
    screen.getByRole("combobox", { name: "Display language" }).textContent,
  ).toBe("English");

  await view.selectLanguage("System");
  expect(
    (await screen.findByRole("combobox", { name: "显示语言" })).textContent,
  ).toBe("系统");
  expect(localStorage.getItem(preferenceKey)).toBe("system");
  view.unmount();

  const restored = await mountSettings();
  expect(screen.getByRole("combobox", { name: "显示语言" }).textContent).toBe(
    "系统",
  );
  restored.unmount();

  languages.mockReturnValue(["en-US"]);
  await mountSettings();
  expect(
    screen.getByRole("combobox", { name: "Display language" }).textContent,
  ).toBe("System");
  expect(localStorage.getItem(preferenceKey)).toBe("system");
});

it("still switches language when browser storage cannot be read or written", async () => {
  for (const method of ["getItem", "setItem"] as const) {
    vi.spyOn(Storage.prototype, method).mockImplementation(() => {
      throw new DOMException("Storage is unavailable", "SecurityError");
    });
  }
  const { selectLanguage } = await mountSettings();
  expect(
    screen.getByRole("combobox", { name: "Display language" }),
  ).toBeTruthy();

  await selectLanguage("简体中文");
  expect(
    await screen.findByRole("combobox", { name: "显示语言" }),
  ).toBeTruthy();
  expect(screen.getByText("偏好设置会自动保存在此浏览器中。")).toBeTruthy();
});
