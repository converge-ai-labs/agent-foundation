import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Button,
  Checkbox,
  ModalFrame,
  FormField,
  Input,
  Label,
  Switch,
} from "../src";
describe("component interaction contracts", () => {
  it("keeps button content mounted and prevents submission while loading", async () => {
    const click = vi.fn();
    const ref = createRef<HTMLButtonElement>();
    const { rerender } = render(
      <Button ref={ref} loading={false} onClick={click}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(ref.current).toBe(button);

    expect(button.getAttribute("type")).toBe("button");
    const content = button.textContent;
    rerender(
      <Button ref={ref} loading onClick={click}>
        Save
      </Button>,
    );
    expect(button.textContent).toBe(content);
    expect(button.hasAttribute("disabled")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(click).not.toHaveBeenCalled();
  });
  it("associates labels, hints, external descriptions and validation errors", () => {
    render(
      <>
        <p id="external">External help</p>
        <FormField label="Name" description="Short name" error="Required">
          <Input aria-describedby="external" />
        </FormField>
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
        <Label>
          <Checkbox />
          Check
        </Label>
        <Label>
          <Switch />
          Switch
        </Label>
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
      <ModalFrame
        trigger={<Button>Open</Button>}
        title="Preferences"
        description="Change preferences"
        closeLabel="Close"
      >
        <FormField label="Name">
          <Input />
        </FormField>
      </ModalFrame>,
    );
    const trigger = screen.getByRole("button", { name: "Open" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Preferences" })).toBeTruthy();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
});

it("exposes a named search field and preserves native editing and description semantics", async () => {
  const user = userEvent.setup(),
    ref = createRef<HTMLInputElement>();
  render(
    <>
      <p id="search-help">Search the current page</p>
      <Input
        type="search"
        aria-label="Find agents"
        ref={ref}
        aria-describedby="search-help"
      />
    </>,
  );
  const input = screen.getByRole("searchbox", { name: "Find agents" });
  expect(ref.current).toBe(input);
  expect(input.getAttribute("aria-describedby")).toBe("search-help");
  await user.type(input, "research");
  expect(ref.current?.value).toBe("research");
});
