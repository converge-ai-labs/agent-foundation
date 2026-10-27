import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DetailHeader } from "./detail";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => vi.restoreAllMocks());

it("expands and collapses clipped descriptions with the keyboard and resets for a new description", async () => {
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(120);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(40);
  const user = userEvent.setup();
  const view = render(
    <DetailHeader
      name="Schedule"
      description="Long description"
      expandableDescription
    />,
  );
  const button = screen.getByRole("button", { name: "Expand description" });
  const text = screen.getByText("Long description");
  expect(button.getAttribute("aria-controls")).toBe(text.id);
  expect(button.getAttribute("aria-expanded")).toBe("false");
  await user.tab();
  await user.keyboard("{Enter}");
  expect(
    screen
      .getByRole("button", { name: "Collapse description" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  expect(text.getAttribute("data-expanded")).toBe("true");
  await user.keyboard(" ");
  expect(button.getAttribute("aria-expanded")).toBe("false");
  await user.click(button);
  view.rerender(
    <DetailHeader
      name="Another schedule"
      description="Another description"
      expandableDescription
    />,
  );
  expect(
    screen
      .getByRole("button", { name: "Expand description" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
});

it("only offers expansion when the rendered description overflows, including after resize", () => {
  let height = 40;
  let resize = () => {};
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(
    () => height,
  );
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(40);
  const disconnect = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe() {}
      disconnect = disconnect;
    },
  );
  const view = render(
    <DetailHeader
      name="Schedule"
      description="Description"
      expandableDescription
    />,
  );
  expect(screen.queryByRole("button")).toBeNull();
  act(() => {
    height = 80;
    resize();
  });
  expect(
    screen.getByRole("button", { name: "Expand description" }),
  ).toBeTruthy();
  act(() => {
    height = 40;
    resize();
  });
  expect(screen.queryByRole("button")).toBeNull();
  view.unmount();
  expect(disconnect).toHaveBeenCalled();
});
