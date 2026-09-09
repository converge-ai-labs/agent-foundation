// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { expect, it, vi } from "vitest";
import { DateTimeField } from "./date-time-field";
import { formatLocalDateTime, parseLocalDateTime } from "./local-date-time";

function setup(locale = "en") {
  const change = vi.fn();
  const i18n = createInstance();
  void i18n.init({
    lng: locale,
    resources: { en: { translation: {} }, "zh-CN": { translation: {} } },
    initAsync: false,
  });
  render(
    <I18nextProvider i18n={i18n}>
      <DateTimeField
        label="Expiration date"
        value="2026-09-09T14:30"
        onValueChange={change}
      />
    </I18nextProvider>,
  );
  return { change, user: userEvent.setup() };
}
it("keeps selection as a draft until Apply and preserves the chosen time", async () => {
  const { user, change } = setup();
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  const picker = within(
    screen.getByRole("dialog", { name: "Expiration date" }),
  );
  await user.click(picker.getByRole("button", { name: /September 10/ }));
  expect(change).not.toHaveBeenCalled();
  await user.click(picker.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenCalledExactlyOnceWith("2026-09-10T14:30");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Expiration date" }),
  );
});
it("discards an escaped draft and supports clearing an optional date", async () => {
  const { user, change } = setup();
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  await user.click(screen.getByRole("button", { name: /September 10/ }));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(change).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenLastCalledWith("2026-09-09T14:30");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  await user.click(screen.getByRole("button", { name: "Clear" }));
  expect(change).toHaveBeenLastCalledWith("");
});
it("uses the explicitly supplied locale for calendar headings", async () => {
  const { user } = setup("zh-CN");
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  const picker = screen.getByRole("dialog", { name: "Expiration date" });
  expect(picker.textContent).toContain("2026年9月");
  expect(picker.textContent).not.toContain("September");
});
it("rejects nonexistent dates and round-trips a valid local time", () => {
  expect(parseLocalDateTime("2026-02-30T14:30")).toBeUndefined();
  expect(parseLocalDateTime("2026-09-09T25:00")).toBeUndefined();
  expect(parseLocalDateTime("")).toBeUndefined();
  expect(formatLocalDateTime(parseLocalDateTime("2024-02-29T14:30")!)).toBe(
    "2024-02-29T14:30",
  );
});
