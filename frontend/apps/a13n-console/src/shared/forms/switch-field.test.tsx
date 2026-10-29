import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SwitchField } from "./switch-field";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("associates the explanation and clickable label with its switch", async () => {
  const change = vi.fn();
  render(
    <SwitchField
      label="Context summary"
      description="Continue with a fresh context."
      checked={false}
      onCheckedChange={change}
    />,
  );
  const control = screen.getByRole("switch", { name: "Context summary" });
  const hint = document.getElementById(
    control.getAttribute("aria-describedby")!,
  );
  expect(hint?.textContent).toBe("Continue with a fresh context.");
  await userEvent.click(screen.getByText("Context summary"));
  expect(change).toHaveBeenCalledWith(true, expect.anything());
});

it("preserves disabled and read-only settings without allowing changes", async () => {
  const change = vi.fn();
  const { rerender } = render(
    <SwitchField
      label="Context summary"
      checked
      disabled
      onCheckedChange={change}
    />,
  );
  await userEvent.click(screen.getByText("Context summary"));
  expect(change).not.toHaveBeenCalled();
  rerender(
    <SwitchField
      label="Context summary"
      checked
      readOnly
      onCheckedChange={change}
    />,
  );
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.getByRole("group", { name: "Context summary" })).toBeTruthy();
  expect(screen.getByText("On")).toBeTruthy();
});
