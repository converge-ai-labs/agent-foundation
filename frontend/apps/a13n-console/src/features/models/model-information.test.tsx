import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { characteristicsInput, ModelInformation } from "./model-information";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("preserves independent file, direct URL, and YouTube declarations when editing", () => {
  const capabilities = [
    "video_understanding",
    "video_url_understanding",
    "youtube_url_understanding",
  ] as const;
  expect(
    characteristicsInput({ capabilities: [...capabilities] }).capabilities,
  ).toEqual(capabilities);
});

it("edits YouTube support without enabling files or arbitrary video URLs", () => {
  const onChange = vi.fn();
  render(<ModelInformation value={{ capabilities: [] }} onChange={onChange} />);
  fireEvent.click(screen.getByRole("switch", { name: "YouTube URLs" }));
  expect(onChange).toHaveBeenCalledWith({
    capabilities: ["youtube_url_understanding"],
  });
  expect(
    screen
      .getByRole("switch", { name: "External video URLs" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(
    screen
      .getByRole("switch", { name: "Video files" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});
