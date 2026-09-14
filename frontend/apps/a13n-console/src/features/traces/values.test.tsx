import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { Duration, Level, TelemetryStatus } from "./values";
import { contentPreview } from "./preview";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("always displays seconds, preserving short and zero durations and missing timing", () => {
  for (const [end, expected] of [
    ["2026-09-11T00:00:01.234Z", "1.234 s"],
    ["2026-09-11T00:00:00.001Z", "0.001 s"],
    ["2026-09-11T00:00:00Z", "0 s"],
    ["2026-09-10T23:59:59Z", "-"],
    [null, "-"],
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
    const { container, unmount } = render(<Level level={raw} />);
    expect(container.textContent).toBe(label);
    expect(container.querySelector("[title]")?.getAttribute("title")).toBe(raw);
    unmount();
  }
  const { container } = render(
    <>
      <Level level={null} />
      <TelemetryStatus
        observation={{ status: null } as Schema["Observation"]}
      />
    </>,
  );
  expect(container.textContent).toBe("--");
});

it("previews root blocks and encoded messages with bounded plain text and truthful empty values", () => {
  const preview = (value: unknown) =>
    contentPreview({ media_type: "application/json", value: value as never });
  expect(contentPreview(null)).toBe("-");
  expect(preview(null)).toBe("null");
  expect(preview(0)).toBe("0");
  expect(preview(false)).toBe("false");
  expect(preview("")).toBe('""');
  expect(preview([])).toBe("[]");
  expect(
    preview({
      schema_version: 1,
      content: [{ type: "text", text: "Hello\nworld" }],
    }),
  ).toBe("Hello world");
  expect(
    preview(
      JSON.stringify({
        messages: [
          { role: "user", parts: [{ type: "text", content: "Question" }] },
        ],
      }),
    ),
  ).toBe("Question");
  expect(preview({ opaque: 7 })).toBe('{"opaque":7}');
  expect(preview('<img src="https://example.com/pixel">')).toContain("<img");
  expect(preview('{"id":9007199254740993}')).toContain("9007199254740993");
  expect(preview("a".repeat(100_000))).toHaveLength(241);
});
