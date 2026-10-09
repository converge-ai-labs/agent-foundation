import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  characteristicsInput,
  characteristicsDefaults,
  ModelInformation,
} from "./model-information";

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

it("keeps PDF opt-in in defaults without changing saved declarations or other facts", () => {
  const facts = {
    capabilities: ["image_understanding", "document_understanding"],
    context_window_tokens: 1050000,
  };
  expect(characteristicsDefaults(facts)).toEqual({
    ...facts,
    capabilities: ["image_understanding"],
  });
  expect(characteristicsInput(facts)).toEqual(facts);
  expect(characteristicsDefaults(null).capabilities).toBeUndefined();
  const onChange = vi.fn();
  render(
    <ModelInformation
      value={characteristicsDefaults(facts)}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByRole("switch", { name: "PDF documents" }));
  expect(onChange).toHaveBeenCalledWith(facts);
});
