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
import { AppearanceProvider } from "../appearance/react";
import { AppearanceStore } from "../appearance/store";
import { AppearanceSettings } from "./appearance";
import { i18n } from "../i18n";
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});
it("edits independent sizes, resets them, and reports persistence failure", async () => {
  await i18n.changeLanguage("en");
  const store = new AppearanceStore(window);
  render(
    <AppearanceProvider store={store}>
      <AppearanceSettings />
    </AppearanceProvider>,
  );
  expect(
    screen.queryByRole("button", { name: "Reset Code font size" }),
  ).toBeNull();
  const code = screen.getByRole("spinbutton", { name: "Code" });
  fireEvent.change(code, { target: { value: "18" } });
  expect(store.getSnapshot().appearance.sizes.code).toBe(18);
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Conversation",
      }) as HTMLInputElement
    ).value,
  ).toBe("15");
  fireEvent.change(code, { target: { value: "99" } });
  fireEvent.blur(code);
  expect((code as HTMLInputElement).value).toBe("18");
  await userEvent.click(
    screen.getByRole("button", { name: "Reset Code font size" }),
  );
  expect((code as HTMLInputElement).value).toBe("12");
  expect(
    screen.queryByRole("button", { name: "Reset Code font size" }),
  ).toBeNull();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  await userEvent.click(screen.getByRole("combobox", { name: "Color theme" }));
  await userEvent.click(await screen.findByRole("option", { name: "Dark" }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(screen.getByRole("status").textContent).toContain("this page only");
  act(() => store.setSize("navigation", 16));
  await userEvent.click(
    screen.getByRole("button", { name: "Reset appearance" }),
  );
  expect(store.getSnapshot().appearance.sizes.navigation).toBe(13);
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});
