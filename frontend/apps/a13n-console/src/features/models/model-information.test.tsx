import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { characteristicsInput, ModelInformation } from "./model-information";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("preserves binary, native URL and byte-budget declarations when editing", () => {
  const value = {
    capabilities: ["video_understanding"] as const,
    url_input: { video: ["youtube"] as const },
    video_input: { max_video_bytes: 10485760 },
  };
  expect(
    characteristicsInput({
      ...value,
      capabilities: [...value.capabilities],
      url_input: { video: [...value.url_input.video] },
    }),
  ).toEqual(value);
});

it("edits native YouTube URL support without enabling video files", () => {
  const onChange = vi.fn();
  render(<ModelInformation value={{ capabilities: [] }} onChange={onChange} />);
  fireEvent.click(screen.getByRole("switch", { name: "YouTube URLs" }));
  expect(onChange).toHaveBeenCalledWith({
    capabilities: [],
    url_input: { video: ["youtube"] },
  });
  expect(
    screen
      .getByRole("switch", { name: "Video files" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});
