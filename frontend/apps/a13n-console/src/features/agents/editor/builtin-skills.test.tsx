import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ListRows } from "../../../shared/collection";
import { BuiltinSkillRows } from "./builtin-skills";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("shows bound instructions in the regular skill list without independent controls", () => {
  render(
    <ListRows>
      <BuiltinSkillRows names={["platform-operations"]} />
    </ListRows>,
  );
  expect(screen.getByText("platform-operations")).toBeTruthy();
  expect(
    screen.getByText("Included automatically with enabled platform tools."),
  ).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByText("Built-in skills")).toBeNull();
});

it("does not advertise unselected builtin skills", () => {
  render(
    <ListRows>
      <BuiltinSkillRows names={[]} />
    </ListRows>,
  );
  expect(screen.queryByText("platform-operations")).toBeNull();
});
