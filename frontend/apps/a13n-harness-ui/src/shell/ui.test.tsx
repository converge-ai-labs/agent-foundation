// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { PageHeader } from "./ui";

afterEach(cleanup);

it("supports a heading without a subtitle and retains actions and heading level", () => {
  const view = render(
    <PageHeader
      title="Models"
      level={2}
      actions={<button>Add model</button>}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Models", level: 2 }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add model" })).toBeTruthy();
  expect(view.container.querySelector("p")).toBeNull();
  view.rerender(
    <PageHeader title="Models" description="Connection settings" />,
  );
  expect(screen.getByText("Connection settings")).toBeTruthy();
});
