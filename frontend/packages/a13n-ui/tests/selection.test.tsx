import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button, ModalFrame, SearchPicker, ChoiceField } from "../src";
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
      ...Array.from({ length: 7 }, (_, index) => ({
        value: `location-${index}`,
        label: `Location ${index}`,
      })),
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
  it.each([0, 1, 9])(
    "opens setup from a %i-option picker footer and restores trigger focus",
    async (count) => {
      const user = userEvent.setup();
      function Example() {
        const [open, setOpen] = useState(false);
        const [adding, setAdding] = useState(false);
        const trigger = useRef<HTMLButtonElement>(null);
        return (
          <>
            <SearchPicker
              label="Model"
              placeholder="Choose a model"
              emptyMessage="No models"
              open={open}
              onOpenChange={setOpen}
              triggerRef={trigger}
              onValueChange={() => {}}
              groups={[
                {
                  label: "Models",
                  options: Array.from({ length: count }, (_, i) => ({
                    value: `model-${i}`,
                    label: `Model ${i}`,
                  })),
                },
              ]}
              footer={
                <Button
                  onClick={() => {
                    setOpen(false);
                    setAdding(true);
                  }}
                >
                  Add model
                </Button>
              }
            />
            <ModalFrame
              open={adding}
              onOpenChange={setAdding}
              finalFocus={trigger}
              title="Model setup"
              closeLabel="Close"
            >
              <input aria-label="Model name" />
            </ModalFrame>
          </>
        );
      }
      render(<Example />);
      const trigger = screen.getByRole("combobox", { name: "Model" });
      trigger.focus();
      await user.keyboard("{ArrowDown}");
      if (count > 8) {
        const search = within(
          await screen.findByRole("dialog", { name: "Model" }),
        ).getByRole("combobox");
        await user.type(search, "no match");
        expect(screen.getByText("No models")).toBeTruthy();
      }
      await user.click(screen.getByRole("button", { name: "Add model" }));
      await screen.findByRole("dialog", { name: "Model setup" });
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      await user.click(screen.getByRole("button", { name: "Close" }));
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    },
  );
  it("selects short local lists without a search input", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <SearchPicker
        label="Status"
        placeholder="All statuses"
        emptyMessage="No statuses"
        value="all"
        onValueChange={change}
        groups={[
          {
            label: "",
            options: [
              { value: "all", label: "All statuses" },
              { value: "enabled", label: "Enabled" },
            ],
          },
        ]}
      />,
    );
    const trigger = screen.getByRole("combobox", { name: "Status" });
    await user.click(trigger);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    await user.click(await screen.findByRole("option", { name: "Enabled" }));
    expect(change).toHaveBeenCalledWith("enabled");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
  it("retains search when input drives remote lookup or custom values", async () => {
    const user = userEvent.setup();
    const searchChanged = vi.fn();
    render(
      <SearchPicker
        label="Service"
        placeholder="Search or paste URL"
        emptyMessage="No services"
        onValueChange={() => {}}
        onSearchChange={searchChanged}
        groups={[]}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "Service" }));
    const search = within(
      await screen.findByRole("dialog", { name: "Service" }),
    ).getByRole("combobox");
    await user.type(search, "https://example.com/mcp");
    expect(searchChanged).toHaveBeenLastCalledWith("https://example.com/mcp");
  });
  it("keeps an inline filter named and updates its selected value", async () => {
    const user = userEvent.setup();
    function Filter() {
      const [value, setValue] = useState("all");
      return (
        <ChoiceField
          label="Status"
          variant="filter"
          value={value}
          onValueChange={setValue}
          options={[
            { value: "all", label: "All" },
            { value: "ready", label: "Ready" },
          ]}
        />
      );
    }
    render(<Filter />);
    const trigger = screen.getByRole("combobox", { name: "Status" });
    expect(trigger.textContent).toBe("StatusAll");
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name: "Ready" }));
    expect(trigger.textContent).toBe("StatusReady");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
  it("keeps field semantics separate from the standalone control", () => {
    render(
      <ChoiceField
        label="Location"
        placeholder="Choose"
        description="Select a location"
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
        <SearchPicker
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
    const trigger = screen.getByRole("combobox", { name: "Location" });
    await user.click(trigger);
    const search = within(
      await screen.findByRole("dialog", { name: "Location" }),
    ).getByRole("combobox");
    await waitFor(() => expect(document.activeElement).toBe(search));
    await user.type(search, "教程");
    expect(screen.getByRole("option", { name: /Guides/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Private/ })).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger.textContent).toBe("Guides");
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    expect(
      (
        within(
          await screen.findByRole("dialog", { name: "Location" }),
        ).getByRole("combobox") as HTMLInputElement
      ).value,
    ).toBe("");
  });
  it("distinguishes same-name options and searches their source badges", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <SearchPicker
        label="Service"
        placeholder="Search services"
        emptyMessage="No services"
        onValueChange={change}
        onSearchChange={vi.fn()}
        groups={[
          {
            label: "",
            options: [
              {
                value: "remote-github",
                label: "GitHub",
                badge: "Remote MCP",
                description: "Repositories and issues",
              },
              {
                value: "composio-github",
                label: "GitHub",
                badge: "Composio · Team",
                description: "Repositories and issues",
              },
            ],
          },
        ]}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "Service" }));
    const search = within(
      await screen.findByRole("dialog", { name: "Service" }),
    ).getByRole("combobox");
    expect(
      screen.getByRole("option", { name: /GitHub Remote MCP/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole("option", { name: /GitHub Composio/ }),
    ).toBeTruthy();
    await user.type(search, "Composio");
    expect(screen.queryByRole("option", { name: /Remote MCP/ })).toBeNull();
    await user.keyboard("{Enter}");
    expect(change).toHaveBeenCalledWith("composio-github");
  });
  it("shows empty results and cannot activate a disabled result", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <SearchPicker
        label="Location"
        placeholder="Search"
        emptyMessage="No locations"
        onValueChange={change}
        groups={groups}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "Location" }));
    const search = within(
      await screen.findByRole("dialog", { name: "Location" }),
    ).getByRole("combobox");
    await waitFor(() => expect(document.activeElement).toBe(search));
    await user.type(search, "zzzz");
    expect(screen.getByText("No locations")).toBeTruthy();
    await user.keyboard("{Enter}");
    expect(change).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("combobox", { name: "Location" }));
    const reopened = within(
      await screen.findByRole("dialog", { name: "Location" }),
    ).getByRole("combobox");
    await waitFor(() => expect(document.activeElement).toBe(reopened));
    await user.type(reopened, "Private");
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
      <ModalFrame
        trigger={<Button>Open</Button>}
        title="Preferences"
        description="Edit preferences"
        closeLabel="Close"
      >
        <SearchPicker
          label="Location"
          placeholder="Search"
          emptyMessage="No locations"
          onValueChange={() => {}}
          groups={groups}
        />
      </ModalFrame>,
    );
    await user.click(screen.getByRole("button", { name: "Open" }));
    const trigger = screen.getByRole("combobox", { name: "Location" });
    await user.click(trigger);
    const innerSearch = within(
      await screen.findByRole("dialog", { name: "Location" }),
    ).getByRole("combobox");
    await waitFor(() => expect(document.activeElement).toBe(innerSearch));
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Location" })).toBeNull(),
    );
    expect(screen.getByRole("dialog", { name: "Preferences" })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
