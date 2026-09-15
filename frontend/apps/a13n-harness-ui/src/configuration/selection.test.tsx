// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SelectionField } from "./selection";

afterEach(cleanup);
it("opens an empty custom selection without enabling a resource and keeps the last unchecked item editable", async () => {
  function Fields() {
    const [value, setValue] = useState<string[] | undefined>();
    return (
      <>
        <SelectionField
          label="Plugins"
          value={value}
          onChange={setValue}
          options={[{ value: "plugin-one", label: "One" }]}
        />
        <output>{JSON.stringify(value) ?? "inherited"}</output>
      </>
    );
  }
  render(<Fields />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox"));
  await user.click(
    await screen.findByRole("option", { name: "Custom selection" }),
  );
  expect(screen.getByRole("checkbox").getAttribute("checked")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("[]");
  fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
  expect(screen.getByRole("status").textContent).toBe('["plugin-one"]');
  fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
  expect(screen.getByRole("checkbox")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("[]");
  fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
  await user.click(await screen.findByRole("option", { name: "Use default" }));
  expect(screen.getByRole("status").textContent).toBe("inherited");
});

it("keeps unavailable selections removable, filters without changing the list, and distinguishes None from inherited", async () => {
  function Fields() {
    const [value, setValue] = useState<string[] | undefined>([
      "plugin-missing",
      "plugin-3",
    ]);
    return (
      <>
        <SelectionField
          label="Plugins"
          value={value}
          onChange={setValue}
          options={Array.from({ length: 10 }, (_, i) => ({
            value: `plugin-${i}`,
            label: `Plugin ${i}`,
          }))}
        />
        <output>{JSON.stringify(value) ?? "inherited"}</output>
      </>
    );
  }
  render(<Fields />);
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox"), "plugin-3");
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  expect(screen.getByRole("status").textContent).toBe(
    '["plugin-missing","plugin-3"]',
  );
  await user.clear(screen.getByRole("searchbox"));
  await user.click(
    screen.getByRole("checkbox", { name: "plugin-missing (unavailable)" }),
  );
  expect(screen.getByRole("status").textContent).toBe('["plugin-3"]');
  await user.click(screen.getByRole("combobox", { name: "Plugins" }));
  await user.click(await screen.findByRole("option", { name: "None" }));
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("[]");
  await user.click(screen.getByRole("combobox", { name: "Plugins" }));
  await user.click(await screen.findByRole("option", { name: "Use default" }));
  expect(screen.getByRole("status").textContent).toBe("inherited");
});
