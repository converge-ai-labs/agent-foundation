import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { Duration, TelemetryStatus, TracePill } from "./values";
import { UNKNOWN } from "../../shared/unknown";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("always displays seconds, preserving short and zero durations and missing timing", () => {
  for (const [end, expected] of [
    ["2026-09-11T00:00:01.234Z", "1.234 s"],
    ["2026-09-11T00:00:00.001Z", "0.001 s"],
    ["2026-09-11T00:00:00Z", "0 s"],
    ["2026-09-10T23:59:59Z", UNKNOWN],
    [null, UNKNOWN],
  ]) {
    const { container, unmount } = render(
      <Duration
        observation={
          {
            started_at: "2026-09-11T00:00:00Z",
            ended_at: end,
          } as Schema["Observation"]
        }
      />,
    );
    expect(container.textContent).toBe(expected);
    unmount();
  }
});

it("normalizes every known level label without rewriting raw or unfamiliar levels", () => {
  for (const [raw, label] of [
    ["default", "Info"],
    ["DEBUG", "Debug"],
    ["info", "Info"],
    ["trace", "Trace level"],
    ["notice", "Notice"],
    ["warn", "Warning"],
    ["warning", "Warning"],
    ["error", "Error"],
    ["critical", "Critical"],
    ["fatal", "Fatal"],
    ["vendor-alert", "vendor-alert"],
  ]) {
    const { container, unmount } = render(<TracePill level={raw} />);
    expect(container.textContent).toBe(label);
    expect(container.querySelector("[title]")?.getAttribute("title")).toBe(raw);
    unmount();
  }
  const { container } = render(
    <>
      <TracePill level={null} />
      <TelemetryStatus
        observation={{ status: null } as Schema["Observation"]}
      />
    </>,
  );
  expect(container.textContent).toBe(`${UNKNOWN}${UNKNOWN}`);
});
