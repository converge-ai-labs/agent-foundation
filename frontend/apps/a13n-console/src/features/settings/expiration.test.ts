import { expect, it } from "vitest";
import { expirationTimestamp } from "./expiration";
it("preserves no expiration and measures day presets from submission", () => {
  const now = new Date("2026-09-09T07:38:12.345Z");
  expect(expirationTimestamp("never", now)).toBeNull();
  expect(expirationTimestamp("1d", now)).toBe("2026-09-10T07:38:12.345Z");
  expect(expirationTimestamp("7d", now)).toBe("2026-09-16T07:38:12.345Z");
  expect(expirationTimestamp("30d", now)).toBe("2026-10-09T07:38:12.345Z");
});
it("clamps calendar durations at month end and leap day without mutating the clock", () => {
  const now = new Date("2026-01-31T07:38:12.345Z");
  expect(expirationTimestamp("3m", now)).toBe("2026-04-30T07:38:12.345Z");
  expect(expirationTimestamp("1y", new Date("2024-02-29T07:38:00Z"))).toBe(
    "2025-02-28T07:38:00.000Z",
  );
  expect(now.toISOString()).toBe("2026-01-31T07:38:12.345Z");
});
