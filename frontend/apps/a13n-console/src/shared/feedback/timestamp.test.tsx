import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Timestamp } from "./timestamp";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

afterEach(() => vi.useRealTimers());

it("refreshes relative times without a page reload and releases the shared clock", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-21T12:00:00Z"));
  const { unmount } = render(
    <>
      <Timestamp value="2026-10-21T11:59:00Z" relative />
      <Timestamp value="2026-10-21T11:58:00Z" relative />
    </>,
  );
  expect(screen.getByText("1 min. ago")).toBeTruthy();
  expect(vi.getTimerCount()).toBe(1);
  act(() => vi.advanceTimersByTime(60_000));
  expect(screen.queryByText("1 min. ago")).toBeNull();
  expect(screen.getByText("3 min. ago")).toBeTruthy();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
