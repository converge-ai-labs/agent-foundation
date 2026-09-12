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
  fireEvent.click(screen.getByLabelText("One"));
  expect(screen.getByRole("status").textContent).toBe('["plugin-one"]');
  fireEvent.click(screen.getByLabelText("One"));
  expect(screen.getByRole("checkbox")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("[]");
  fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
  await user.click(
    await screen.findByRole("option", { name: "Default (inherit)" }),
  );
  expect(screen.getByRole("status").textContent).toBe("inherited");
});
