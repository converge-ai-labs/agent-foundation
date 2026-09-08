import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button, Dialog, Picker, SelectField } from "../src";
const groups = [
  {
    label: "Workspace",
    options: [
      {
        value: "guide",
        label: "Guides",
        description: "Learning resources",
        keywords: ["教程"],
      },
      {
        value: "private",
        label: "Private",
        description: "Owner access required",
        disabled: true,
      },
    ],
  },
];
describe("selection", () => {
  it("keeps field semantics separate from the standalone control", () => {
    render(
      <SelectField
        label="Location"
        placeholder="Choose"
        hint="Select a location"
        error="Required"
        options={[]}
      />,
    );
    const select = screen.getByRole("combobox", { name: "Location" });
    expect(select.getAttribute("aria-invalid")).toBe("true");
    expect(
      select
        .getAttribute("aria-describedby")!
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent),
    ).toEqual(["Select a location", "Required"]);
  });
  it("searches translated aliases, selects with Enter, and restores focus", async () => {
    const user = userEvent.setup();
    function Example() {
      const [value, setValue] = useState<string>();
      return (
        <Picker
          label="Location"
          placeholder="Find a location"
          emptyMessage="No locations"
          value={value}
          onValueChange={setValue}
          groups={groups}
        />
      );
    }
    render(<Example />);
    const trigger = screen.getByRole("button", { name: "Location" });
    await user.click(trigger);
    const search = screen.getByRole("combobox", { name: "Location" });
    expect(document.activeElement).toBe(search);
    await user.type(search, "教程");
    expect(screen.getByRole("option", { name: /Guides/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Private/ })).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger.textContent).toBe("Guides");
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    expect((screen.getByRole("combobox") as HTMLInputElement).value).toBe("");
  });
  it("shows empty results and cannot activate a disabled result", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <Picker
        label="Location"
        placeholder="Search"
        emptyMessage="No locations"
        onValueChange={change}
        groups={groups}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Location" }));
    const search = screen.getByRole("combobox");
    await user.type(search, "zzzz");
    expect(screen.getByText("No locations")).toBeTruthy();
    await user.keyboard("{Enter}");
    expect(change).not.toHaveBeenCalled();
    await user.clear(search);
    await user.type(search, "Private");
    expect(
      screen
        .getByRole("option", { name: /Private/ })
        .getAttribute("aria-disabled"),
    ).toBe("true");
    await user.keyboard("{Enter}");
    expect(change).not.toHaveBeenCalled();
  });
  it("Escape closes only the inner picker before closing its dialog", async () => {
    const user = userEvent.setup();
    render(
      <Dialog
        trigger={<Button>Open</Button>}
        title="Preferences"
        description="Edit preferences"
        closeLabel="Close"
      >
        <Picker
          label="Location"
          placeholder="Search"
          emptyMessage="No locations"
          onValueChange={() => {}}
          groups={groups}
        />
      </Dialog>,
    );
    await user.click(screen.getByRole("button", { name: "Open" }));
    const trigger = screen.getByRole("button", { name: "Location" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Preferences" })).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
