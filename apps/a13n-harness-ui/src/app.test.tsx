// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { BrowserApp } from "./app";

afterEach(cleanup);

it("renders the Hello World page", () => {
  render(<BrowserApp />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Hello World",
  );
});
