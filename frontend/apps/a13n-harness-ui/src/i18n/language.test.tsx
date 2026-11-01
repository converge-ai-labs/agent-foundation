// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LanguageChoice, LanguageProvider } from "./language";
import { i18n } from "./index";
import { BrowserApp } from "../app";

vi.mock("../shell/workbench", () => ({
  Workbench: () => <h1>Authenticated workspace</h1>,
}));

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  await i18n.changeLanguage("en");
});

async function selectLanguage(name: string) {
  await userEvent.click(screen.getByRole("combobox"));
  await userEvent.click(await screen.findByRole("option", { name }));
}

it("shares preferences between controls, persists them and follows the browser only in System mode", async () => {
  const languages = vi
    .spyOn(navigator, "languages", "get")
    .mockReturnValue(["zh-TW"]);
  const view = render(
    <LanguageProvider>
      <LanguageChoice />
      <LanguageChoice />
    </LanguageProvider>,
  );
  expect(
    await screen.findAllByRole("combobox", { name: "显示语言" }),
  ).toHaveLength(2);
  expect(localStorage.getItem("a13n-harness-ui.language")).toBeNull();
  await userEvent.click(screen.getAllByRole("combobox")[0]);
  await userEvent.click(await screen.findByRole("option", { name: "English" }));
  expect(
    screen
      .getAllByRole("combobox", { name: "Display language" })
      .every((el) => el.textContent === "English"),
  ).toBe(true);
  expect(document.documentElement.lang).toBe("en");
  await act(async () => window.dispatchEvent(new Event("languagechange")));
  expect(document.documentElement.lang).toBe("en");
  view.unmount();
  render(
    <LanguageProvider>
      <LanguageChoice />
    </LanguageProvider>,
  );
  expect(screen.getByRole("combobox").textContent).toBe("English");
  await selectLanguage("System");
  expect(document.documentElement.lang).toBe("zh-CN");
  expect(localStorage.getItem("a13n-harness-ui.language")).toBe("system");
  languages.mockReturnValue(["en-GB"]);
  await act(async () => window.dispatchEvent(new Event("languagechange")));
  expect(document.documentElement.lang).toBe("en");
});

it("retains a manual choice for this session even if browser storage is unavailable", async () => {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);
  for (const method of ["getItem", "setItem"] as const)
    vi.spyOn(Storage.prototype, method).mockImplementation(() => {
      throw new Error("denied");
    });
  render(
    <LanguageProvider>
      <LanguageChoice />
    </LanguageProvider>,
  );
  await selectLanguage("简体中文");
  expect(document.documentElement.lang).toBe("zh-CN");
  await act(async () => window.dispatchEvent(new Event("languagechange")));
  expect(document.documentElement.lang).toBe("zh-CN");
  expect(screen.getByRole("combobox").textContent).toBe("简体中文");
});

it("switches a rejected login and its error without losing the key or replaying authentication", async () => {
  window.history.replaceState(null, "", "/");
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          error: { code: "unauthorized", message: "Unauthorized" },
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<BrowserApp />);
  const field = await screen.findByLabelText("Instance API key");
  fireEvent.change(field, { target: { value: "test-only-key" } });
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));
  await screen.findByText(
    "This instance key wasn't accepted. Check it and try again.",
  );
  const requests = fetcher.mock.calls.length;
  await selectLanguage("简体中文");
  expect(screen.getByRole("heading", { name: "登录 Harness UI" })).toBeTruthy();
  expect(screen.getByLabelText("实例 API 密钥")).toBe(field);
  expect(field).toHaveProperty("value", "test-only-key");
  expect(screen.getByRole("alert").textContent).toContain("实例密钥未通过验证");
  expect(fetcher).toHaveBeenCalledTimes(requests);
  expect(document.documentElement.lang).toBe("zh-CN");
});
