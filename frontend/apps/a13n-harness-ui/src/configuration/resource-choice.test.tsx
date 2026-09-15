// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResourceChoice } from "./resource-choice";

afterEach(cleanup);

it("keeps an unavailable reference visible and only changes it after an explicit choice", async () => {
  const onChange = vi.fn();
  render(
    <ResourceChoice
      row
      label="Default agent"
      value="agent-missing"
      options={[
        { value: "", label: "Use default" },
        { value: "agent-one", label: "Assistant" },
      ]}
      onValueChange={onChange}
      description="Used for new conversations."
    />,
  );
  const trigger = screen.getByRole("combobox", { name: "Default agent" });
  expect(trigger.textContent).toContain("agent-missing (unavailable)");
  expect(trigger.getAttribute("aria-describedby")).toBeTruthy();
  expect(onChange).not.toHaveBeenCalled();
  const user = userEvent.setup();
  await user.click(screen.getByText("Default agent", { selector: "label" }));
  expect(
    await screen.findByRole("option", { name: /agent-missing/ }),
  ).toHaveProperty("ariaDisabled", "true");
  await user.click(screen.getByRole("option", { name: "Use default" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith("");
});

it("searches resource IDs as well as labels and resets search after closing", async () => {
  const onChange = vi.fn();
  render(
    <ResourceChoice
      label="Agent"
      value="agent-0"
      options={Array.from({ length: 12 }, (_, i) => ({
        value: `agent-${i}`,
        label: `Assistant ${i}`,
      }))}
      onValueChange={onChange}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  await user.type(
    within(await screen.findByRole("dialog", { name: "Agent" })).getByRole(
      "combobox",
    ),
    "agent-11",
  );
  expect(
    await screen.findByRole("option", { name: "Assistant 11" }),
  ).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Assistant 2" })).toBeNull();
  await user.click(screen.getByRole("option", { name: "Assistant 11" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith("agent-11");
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  expect(
    await screen.findByRole("option", { name: "Assistant 2" }),
  ).toBeTruthy();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("option")).toBeNull());
});
