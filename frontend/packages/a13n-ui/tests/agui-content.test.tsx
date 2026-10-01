import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AguiContent, readContentParts } from "../src/components/agui-content";

it("validates upstream parts and preserves ordered duplicates without exposing hidden media", () => {
  const image = {
    type: "image",
    source: { type: "url", value: "https://example.com/image.png" },
  };
  const parts = readContentParts([
    { type: "text", text: "Before" },
    image,
    image,
    {
      ...image,
      metadata: { display: false },
      source: { type: "url", value: "https://example.com/hidden.png" },
    },
    {
      type: "document",
      source: { type: "file", value: "provider-id", provider: "openai" },
    },
    {
      type: "image",
      source: { type: "data", value: "private-bytes", mimeType: "image/png" },
    },
    {
      type: "audio",
      source: { type: "url", value: "https://example.com/audio.mp3" },
    },
    {
      type: "video",
      source: { type: "url", value: "https://example.com/video.mp4" },
    },
    {
      type: "document",
      source: { type: "url", value: "https://example.com/document.pdf" },
    },
    { type: "text", text: "After" },
  ])!;
  expect(parts).toHaveLength(10);
  const { container } = render(<AguiContent parts={parts} />);
  expect(screen.getAllByRole("img")).toHaveLength(2);
  expect(container.innerHTML).not.toContain("hidden.png");
  expect(container.innerHTML).not.toContain("private-bytes");
  expect(container.querySelector("audio")?.getAttribute("src")).toBe(
    "https://example.com/audio.mp3",
  );
  expect(container.querySelector("video")?.getAttribute("src")).toBe(
    "https://example.com/video.mp4",
  );
  expect(
    screen.getByRole("link", { name: "Open document" }).getAttribute("href"),
  ).toBe("https://example.com/document.pdf");
  expect(container.textContent).toMatch(/Before.*preview unavailable.*After/);
  expect(
    readContentParts([{ type: "image", source: { type: "url" } }]),
  ).toBeUndefined();
  expect(readContentParts({ arbitrary: "JSON" })).toBeUndefined();
});
