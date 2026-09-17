// @vitest-environment jsdom
import { expect, it } from "vitest";
import { captureReadingAnchor, restoreReadingAnchor } from "./reading-anchor";

it("preserves the visible row offset after earlier content changes height", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<section data-turn-id="one"><div data-reading-anchor="hidden"></div><div data-reading-anchor="visible"></div></section>';
  reader.getBoundingClientRect = () => new DOMRect(0, 100, 500, 600);
  const row = reader.querySelector<HTMLElement>(
    '[data-reading-anchor="visible"]',
  )!;
  let top = 84;
  row.getBoundingClientRect = () => new DOMRect(0, top, 500, 200);
  const anchor = captureReadingAnchor(reader);
  expect(anchor).toEqual({ id: "visible", turn: "one", offset: -16 });
  reader.scrollTop = 300;
  top += 120;
  restoreReadingAnchor(reader, anchor);
  expect(reader.scrollTop).toBe(420);
});

it("falls back to the input when the anchored process row is collapsed", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<section data-turn-id="one"><div data-reading-anchor="process"></div></section>';
  reader.getBoundingClientRect = () => new DOMRect(0, 100, 500, 600);
  reader.querySelector<HTMLElement>("section")!.getBoundingClientRect = () =>
    new DOMRect(0, 180, 500, 100);
  reader.scrollTop = 300;
  restoreReadingAnchor(reader, { id: "process", turn: "one", offset: -20 });
  expect(reader.scrollTop).toBe(364);
});
