import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ChoiceField, FormField, Input } from "../src";

it("renders fixed values as named text while temporary disabled fields remain controls", () => {
  const options = [{ value: "openai", label: "OpenAI" }];
  render(
    <>
      <FormField label="Model key" description="Fixed after creation.">
        <Input value="model-1" readOnly />
      </FormField>
      <ChoiceField label="Provider" value="openai" options={options} readOnly />
      <FormField label="Name">
        <Input value="Example" disabled />
      </FormField>
      <ChoiceField
        label="Pending provider"
        value="openai"
        options={options}
        disabled
      />
    </>,
  );
  expect(screen.queryByRole("textbox", { name: "Model key" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Provider" })).toBeNull();
  expect(
    screen.getByRole("group", { name: "Model key" }).textContent,
  ).toContain("model-1");
  expect(screen.getByRole("group", { name: "Provider" }).textContent).toContain(
    "OpenAI",
  );
  expect(
    screen.getByRole("textbox", { name: "Name" }).matches(":disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("combobox", { name: "Pending provider" })
      .matches(":disabled"),
  ).toBe(true);
});
