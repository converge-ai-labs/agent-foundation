import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, type ReactNode } from "react";
import { I18nextProvider, useTranslation } from "react-i18next";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { i18n } from "../../i18n";
import { readLanguage } from "../../i18n/preferences";
import { Timestamp } from "../../shared/feedback";
import { Composer } from "../conversations/composer";
import { compactNumber } from "../usage/values";
import { LanguageSettings } from "./preferences";

vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: {}, workspace: () => ({}) }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));

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

async function mountSettings(children?: ReactNode) {
  const instance = i18n.cloneInstance();
  await instance.changeLanguage(readLanguage());
  const view = render(
    <I18nextProvider i18n={instance}>
      <LanguageSettings />
      {children}
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
  ["हिन्दी", "hi", "डिस्प्ले भाषा"],
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

function LocalizedFormats() {
  const { t, i18n } = useTranslation();
  return (
    <>
      <Timestamp value="2024-01-02T03:04:00Z" />
      <output data-testid="compact-number">
        {compactNumber(12345, i18n.resolvedLanguage)}
      </output>
      <output data-testid="plural">
        {t("{{count}} sessions", { count: 2 })}
      </output>
    </>
  );
}

it("formats dates, numbers, and plurals with the selected language", async () => {
  const { selectLanguage } = await mountSettings(<LocalizedFormats />);
  const date = new Date("2024-01-02T03:04:00Z");
  const formatDate = (language: string) =>
    new Intl.DateTimeFormat(language, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  const formatNumber = (language: string) =>
    new Intl.NumberFormat(language, {
      notation: "compact",
      maximumSignificantDigits: 3,
    }).format(12345);

  expect(screen.getByText(formatDate("en"))).toBeTruthy();
  expect(screen.getByTestId("compact-number").textContent).toBe(
    formatNumber("en"),
  );
  expect(screen.getByTestId("plural").textContent).toBe("2 sessions");

  await selectLanguage("简体中文");
  expect(await screen.findByText(formatDate("zh-CN"))).toBeTruthy();
  expect(screen.getByTestId("compact-number").textContent).toBe(
    formatNumber("zh-CN"),
  );
  expect(screen.getByTestId("plural").textContent).toBe("2 个会话");

  await selectLanguage("English");
  expect(await screen.findByText(formatDate("en"))).toBeTruthy();
  expect(screen.getByTestId("compact-number").textContent).toBe(
    formatNumber("en"),
  );
  expect(screen.getByTestId("plural").textContent).toBe("2 sessions");
});

it("preserves composer drafts and active work without replaying requests", async () => {
  const mounted = vi.fn();
  const unmounted = vi.fn();
  let finishSubmit: (() => void) | undefined;
  const submit = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finishSubmit = () => resolve();
      }),
  );
  function ActiveWork() {
    useEffect(() => {
      mounted();
      return unmounted;
    }, []);
    return <div>Active work</div>;
  }
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const { selectLanguage } = await mountSettings(
    <QueryClientProvider client={queryClient}>
      <ActiveWork />
      <Composer submit={submit} />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const input = screen.getByRole("textbox", { name: "Message" });
  await user.type(input, "Keep this draft");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));

  await selectLanguage("简体中文");
  expect(screen.getByRole("textbox", { name: "消息" })).toBe(input);
  expect((input as HTMLTextAreaElement).value).toBe("Keep this draft");
  expect(submit).toHaveBeenCalledTimes(1);
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(unmounted).not.toHaveBeenCalled();

  await selectLanguage("English");
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(input);
  expect((input as HTMLTextAreaElement).value).toBe("Keep this draft");
  expect(submit).toHaveBeenCalledTimes(1);
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(unmounted).not.toHaveBeenCalled();

  finishSubmit?.();
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
});
