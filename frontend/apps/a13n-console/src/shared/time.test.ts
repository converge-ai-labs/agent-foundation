import { expect, it } from "vitest";
import { relativeTime } from "./time";

it("formats recent past and future values without losing the direction", () => {
  const now = Date.UTC(2026, 8, 9, 12);
  expect(relativeTime(new Date(now), "en", now)).toBe("now");
  expect(relativeTime(new Date(now - 65_000), "en", now)).toBe("1 min. ago");
  expect(relativeTime(new Date(now + 3_600_000), "en", now)).toBe("in 1 hr.");
  expect(relativeTime(new Date(now - 86_400_000), "en", now)).toBe("yesterday");
  expect(relativeTime(new Date(now - 65_000), "zh-CN", now)).toBe("1分钟前");
});

it("uses a calendar date for older history", () => {
  const now = Date.UTC(2026, 8, 9, 12),
    old = new Date(now - 8 * 86_400_000);
  expect(relativeTime(old, "en", now)).toBe(
    new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(old),
  );
});
