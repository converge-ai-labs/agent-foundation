import { expect, test } from "vitest";
import html from "../index.html?raw";

test("the hero has an accessible name for the illustrated wordmark", () => {
  expect(html).toMatch(/<h1\b[^>]*\baria-label="a13n\b[^"]*"/i);
});
