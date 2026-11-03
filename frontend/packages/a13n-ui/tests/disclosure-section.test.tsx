import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Button, DisclosureSection } from "../src";

it("keeps header actions separate from the disclosure trigger", async () => {
  const remove = vi.fn();
  render(
    <DisclosureSection
      title="Knowledge"
      actions={<Button onClick={remove}>Remove group</Button>}
    >
      <input aria-label="Group name" />
    </DisclosureSection>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Remove group" }));
  expect(remove).toHaveBeenCalledOnce();
  expect(
    screen
      .getByRole("button", { name: "Knowledge" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  await user.click(screen.getByRole("button", { name: "Knowledge" }));
  expect(screen.getByRole("textbox", { name: "Group name" })).toBeTruthy();
});
