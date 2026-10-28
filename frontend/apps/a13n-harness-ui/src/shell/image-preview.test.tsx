// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ImagePreview } from "./image-preview";

afterEach(cleanup);

it("recovers when a failed image is replaced with a new source", () => {
  const close = vi.fn();
  const view = render(
    <ImagePreview src="blob:broken" name="Review image" close={close} />,
  );
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Actual size" })
      .hasAttribute("disabled"),
  ).toBe(true);
  view.rerender(
    <ImagePreview src="blob:replacement" name="Review image" close={close} />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("img").getAttribute("src")).toBe("blob:replacement");
  expect(
    screen
      .getByRole("button", { name: "Actual size" })
      .hasAttribute("disabled"),
  ).toBe(false);
});
