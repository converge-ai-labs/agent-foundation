// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { expect, it, vi } from "vitest";
import { DateTimeField } from "./date-time-field";
import { formatLocalDateTime, parseLocalDateTime } from "../local-date-time";

function setup(
  locale = "en",
  precision: "minute" | "second" = "minute",
  futureOnly = false,
) {
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
        value={
          precision === "second" ? "2026-09-09T14:30:15" : "2026-09-09T14:30"
        }
        precision={precision}
        futureOnly={futureOnly}
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
  expect(within(picker).getByRole("option", { name: "9月" })).toBeTruthy();
});
it("jumps to a future year and month without changing the selected date until confirmation", async () => {
  const { user, change } = setup("en", "second");
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  const picker = within(
    screen.getByRole("dialog", { name: "Expiration date" }),
  );
  const futureYear = String(new Date().getFullYear() + 5);
  await user.selectOptions(
    picker.getByRole("combobox", { name: "Choose the Year" }),
    futureYear,
  );
  await user.selectOptions(
    picker.getByRole("combobox", { name: "Choose the Month" }),
    "1",
  );
  expect(
    picker.getByRole("grid", { name: `February ${futureYear}` }),
  ).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  await user.click(picker.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenLastCalledWith("2026-09-09T14:30:15");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  const reopened = within(
    screen.getByRole("dialog", { name: "Expiration date" }),
  );
  await user.selectOptions(
    reopened.getByRole("combobox", { name: "Choose the Year" }),
    futureYear,
  );
  await user.selectOptions(
    reopened.getByRole("combobox", { name: "Choose the Month" }),
    "1",
  );
  await user.click(reopened.getByRole("button", { name: /February 10/ }));
  await user.click(reopened.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenLastCalledWith(`${futureYear}-02-10T14:30:15`);
});
it("opens from the keyboard and preserves seconds while changing the date and time", async () => {
  const { user, change } = setup("en", "second");
  const trigger = screen.getByRole("button", { name: "Expiration date" });
  trigger.focus();
  await user.keyboard("{Enter}");
  const picker = within(
    screen.getByRole("dialog", { name: "Expiration date" }),
  );
  await user.click(picker.getByRole("button", { name: /September 10/ }));
  for (const [label, value] of [
    ["Hour", "16"],
    ["Minute", "45"],
  ]) {
    await user.click(picker.getByRole("combobox", { name: label }));
    await user.click(await screen.findByRole("option", { name: value }));
  }
  expect(
    picker.getByRole("combobox", { name: "Second" }).textContent,
  ).toContain("15");
  await user.click(picker.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenCalledExactlyOnceWith("2026-09-10T16:45:15");
});
it("rejects nonexistent dates and round-trips a valid local time", () => {
  expect(parseLocalDateTime("2026-02-30T14:30")).toBeUndefined();
  expect(parseLocalDateTime("2026-09-09T25:00")).toBeUndefined();
  expect(parseLocalDateTime("")).toBeUndefined();
  expect(parseLocalDateTime("2026-09-09T14:30:60")).toBeUndefined();
  expect(
    formatLocalDateTime(parseLocalDateTime("2026-09-09T14:30:15")!, "second"),
  ).toBe("2026-09-09T14:30:15");
  expect(formatLocalDateTime(parseLocalDateTime("2024-02-29T14:30")!)).toBe(
    "2024-02-29T14:30",
  );
});

it("disables past dates and time units while leaving future dates unrestricted", async () => {
  vi.spyOn(Date, "now").mockReturnValue(
    new Date(2026, 8, 9, 14, 30, 10).getTime(),
  );
  const { user, change } = setup("en", "second", true);
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  const picker = within(
    screen.getByRole("dialog", { name: "Expiration date" }),
  );
  expect(
    (picker.getByRole("button", { name: /September 8/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  for (const [label, past, future] of [
    ["Hour", "13", "14"],
    ["Minute", "29", "30"],
    ["Second", "10", "11"],
  ]) {
    await user.click(picker.getByRole("combobox", { name: label }));
    expect(
      (await screen.findByRole("option", { name: past })).getAttribute(
        "aria-disabled",
      ),
    ).toBe("true");
    expect(
      screen
        .getByRole("option", { name: future })
        .getAttribute("aria-disabled"),
    ).not.toBe("true");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  }
  await user.click(picker.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenLastCalledWith("2026-09-09T14:30:15");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  await user.click(screen.getByRole("button", { name: /September 10/ }));
  for (const label of ["Hour", "Minute", "Second"]) {
    await user.click(screen.getByRole("combobox", { name: label }));
    await user.click(await screen.findByRole("option", { name: "00" }));
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  }
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(change).toHaveBeenLastCalledWith("2026-09-10T00:00:00");
  vi.restoreAllMocks();
}, 15_000);

it("expires an open selection and blocks applying a time that has just passed", async () => {
  const clock = vi
    .spyOn(Date, "now")
    .mockReturnValue(new Date(2026, 8, 9, 14, 30, 10).getTime());
  const { user, change } = setup("en", "second", true);
  await user.click(screen.getByRole("button", { name: "Expiration date" }));
  clock.mockReturnValue(new Date(2026, 8, 9, 14, 30, 15).getTime());
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(change).not.toHaveBeenCalled();
  expect(
    (screen.getByRole("button", { name: "Apply" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByRole("alert").textContent).toContain(
    "This time has already passed.",
  );
  clock.mockReturnValue(new Date(2026, 8, 10, 0, 0, 0).getTime());
  await waitFor(
    () =>
      expect(
        (
          screen.getByRole("button", {
            name: /September 9/,
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    { timeout: 2000 },
  );
  vi.restoreAllMocks();
});
