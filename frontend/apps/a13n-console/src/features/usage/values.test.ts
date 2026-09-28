import { expect, it } from "vitest";
import { customWindow, localWindow, percent, presetWindow } from "./values";

it("starts presets at local midnight and ends at the supplied instant", () => {
  const now = new Date(2026, 8, 28, 15, 20);
  const window = presetWindow(7, now);
  expect(new Date(window.start)).toEqual(new Date(2026, 8, 22));
  expect(window.end).toBe(now.toISOString());
  expect(
    customWindow(...(Object.values(localWindow(window)) as [string, string])),
  ).toEqual(window);
});

it("refuses reversed, malformed, and overlong ranges", () => {
  expect(customWindow("bad", "bad")).toBeNull();
  expect(customWindow("2026-09-28T00:00", "2026-09-27T00:00")).toBeNull();
  expect(customWindow("2024-09-28T00:00", "2026-09-28T00:00")).toBeNull();
  expect(customWindow("2026-02-30T00:00", "2026-03-03T00:00")).toBeNull();
});

it("distinguishes no cache observations from a zero cache rate", () => {
  expect(percent(null)).toBe("—");
  expect(percent(0)).toBe("0.0%");
  expect(percent(0.25)).toBe("25.0%");
});
