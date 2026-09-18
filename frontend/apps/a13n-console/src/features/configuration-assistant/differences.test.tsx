import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Differences } from "./differences";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
it("shows added configuration without an Absent placeholder and distinguishes null from a missing value", () => {
  render(
    <Differences
      title="Source"
      changes={[
        {
          path: [],
          before: null,
          before_present: false,
          after: { enabled: true },
          after_present: true,
        },
      ]}
    />,
  );
  expect(screen.getByText("Added configuration")).toBeTruthy();
  expect(screen.queryByText("Absent")).toBeNull();
  expect(screen.getByText('"enabled": true')).toBeTruthy();
  cleanup();
  render(
    <Differences
      title="Source"
      changes={[
        {
          path: ["setting"],
          before: null,
          before_present: true,
          after: "null",
          after_present: true,
        },
      ]}
    />,
  );
  expect(screen.getByText("null")).toBeTruthy();
  expect(screen.getByText('"null"')).toBeTruthy();
  expect(screen.queryByText("Added configuration")).toBeNull();
});
it("renders raw instruction lines, folds unchanged context, and can expand it and switch comparison layout", async () => {
  const user = userEvent.setup();
  const context = Array.from({ length: 15 }, (_, i) => `Context ${i}`).join(
    "\n",
  );
  render(
    <Differences
      title="Changes to apply"
      changes={[
        {
          path: ["instructions"],
          before: `${context}\nOld policy`,
          before_present: true,
          after: `${context}\nNew policy`,
          after_present: true,
        },
      ]}
    />,
  );
  expect(screen.getByText("Old policy")).toBeTruthy();
  expect(screen.getByText("New policy")).toBeTruthy();
  expect(screen.queryByText("Context 7")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Show unchanged lines" }),
  );
  expect(screen.getByText("Context 7")).toBeTruthy();
  await user.click(screen.getByRole("tab", { name: "Side-by-side diff" }));
  expect(screen.getByText("Before")).toBeTruthy();
  expect(screen.getByText("After")).toBeTruthy();
  expect(screen.getByText("Old policy")).toBeTruthy();
  expect(screen.getByText("New policy")).toBeTruthy();
});
it("marks removals without treating false or zero as absent", () => {
  render(
    <Differences
      title="Changes"
      changes={[
        {
          path: ["enabled"],
          before: false,
          before_present: true,
          after: null,
          after_present: false,
        },
        {
          path: ["limit"],
          before: 0,
          before_present: true,
          after: 1,
          after_present: true,
        },
      ]}
    />,
  );
  expect(screen.getByText("Removed configuration")).toBeTruthy();
  expect(screen.getByText("false")).toBeTruthy();
  expect(screen.getByText("0", { selector: "code" })).toBeTruthy();
  expect(screen.getByText("1", { selector: "code" })).toBeTruthy();
});
