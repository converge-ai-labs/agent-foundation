import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button, Checkbox, Dialog, Input, Switch } from "../src";
describe("component interaction contracts", () => {
  it("keeps button content mounted and prevents submission while loading", async () => {
    const click = vi.fn();
    const ref = createRef<HTMLButtonElement>();
    const { rerender } = render(
      <Button ref={ref} loading={false} loadingLabel="Saving" onClick={click}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(ref.current).toBe(button);
    expect(button.getAttribute("type")).toBe("button");
    const content = button.textContent;
    rerender(
      <Button ref={ref} loading loadingLabel="Saving" onClick={click}>
        Save
      </Button>,
    );
    expect(button.textContent).toBe(content);
    expect(button.getAttribute("aria-busy")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "Saving" }));
    expect(click).not.toHaveBeenCalled();
  });
  it("associates labels, hints, external descriptions and validation errors", () => {
    render(
      <>
        <p id="external">External help</p>
        <Input
          label="Name"
          hint="Short name"
          error="Required"
          aria-describedby="external"
        />
      </>,
    );
    const input = screen.getByRole("textbox", { name: "Name" });
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const ids = input.getAttribute("aria-describedby")!.split(" ");
    expect(ids.map((id) => document.getElementById(id)?.textContent)).toEqual([
      "External help",
      "Short name",
      "Required",
    ]);
  });
  it("supports keyboard toggles", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Checkbox label="Check" />
        <Switch label="Switch" />
      </>,
    );
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByRole("checkbox").getAttribute("aria-checked")).toBe(
      "true",
    );
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe(
      "true",
    );
  });
  it("opens a named dialog and restores focus after Escape", async () => {
    const user = userEvent.setup();
    render(
      <Dialog
        trigger={<Button>Open</Button>}
        title="Preferences"
        description="Change preferences"
        closeLabel="Close"
      >
        <Input label="Name" />
      </Dialog>,
    );
    const trigger = screen.getByRole("button", { name: "Open" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Preferences" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
