import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ObservationTree } from "./observation-tree";
import { observationRows } from "./timeline";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined
        ? key
        : key.replace("{{count}}", String(options.count)),
  }),
}));
afterEach(cleanup);

function observation(id: string, parent: string | null): Schema["Observation"] {
  return {
    id,
    parent_id: parent,
    type: "span",
    name: id,
    started_at: "2026-09-11T00:00:00Z",
    ended_at: "2026-09-11T00:00:01Z",
    status: "ok",
    level: null,
    status_message: null,
    model: null,
    usage: null,
    cost_usd: null,
    input: null,
    output: null,
    attributes: {},
    resource_attributes: null,
    scope: null,
    events: null,
    links: null,
  };
}

const observations = [
  observation("root", null),
  observation("branch", "root"),
  observation("leaf", "branch"),
  observation("sibling", "root"),
];

function mount(tree = true) {
  const onSelect = vi.fn();
  const rows = tree
    ? observationRows(observations)
    : observations.map((item) => ({
        observation: item,
        depth: 0,
        childCount: 0,
      }));
  render(
    <ObservationTree
      rows={rows}
      tree={tree}
      start={Date.parse("2026-09-11T00:00:00Z")}
      duration={1000}
      loaded={new Set(observations.map((item) => item.id))}
      selectedId={null}
      onSelect={onSelect}
    />,
  );
  return onSelect;
}

/** Rows in view, by the observation name each row displays. */
const names = () =>
  screen
    .getAllByRole("treeitem")
    .map((row) => row.querySelector("span[title]")?.getAttribute("title"));

const row = (name: RegExp) => screen.getByRole("treeitem", { name });
const caret = (name: RegExp) =>
  row(name).firstElementChild!.firstElementChild as HTMLElement;
const focused = () => document.activeElement?.querySelector("span[title]");

it("collapses a subtree from the caret and counts the hidden children", async () => {
  const user = userEvent.setup();
  mount();
  expect(names()).toEqual(["root", "branch", "leaf", "sibling"]);
  expect(row(/^branch/).getAttribute("aria-level")).toBe("2");
  expect(caret(/^branch/).hasAttribute("data-open")).toBe(true);
  await user.click(caret(/^branch/));
  expect(names()).toEqual(["root", "branch", "sibling"]);
  const branch = row(/^branch/);
  expect(branch.getAttribute("aria-expanded")).toBe("false");
  expect(branch.textContent).toContain("1 nested");
  await user.click(caret(/^branch/));
  expect(names()).toEqual(["root", "branch", "leaf", "sibling"]);
});

it("selects a row on click and leaves childless rows without a caret state", async () => {
  const user = userEvent.setup();
  const onSelect = mount();
  await user.click(row(/^leaf/));
  expect(onSelect).toHaveBeenCalledWith("leaf");
  expect(row(/^leaf/).getAttribute("aria-expanded")).toBeNull();
});

it("moves, collapses and opens rows from the keyboard behind one tab stop", async () => {
  const user = userEvent.setup();
  const onSelect = mount();
  expect(
    screen
      .getAllByRole("treeitem")
      .map((item) => item.getAttribute("tabindex")),
  ).toEqual(["0", "-1", "-1", "-1"]);
  await user.tab();
  expect(focused()?.getAttribute("title")).toBe("root");
  await user.keyboard("{ArrowDown}");
  expect(focused()?.getAttribute("title")).toBe("branch");
  await user.keyboard("{ArrowLeft}");
  expect(names()).toEqual(["root", "branch", "sibling"]);
  await user.keyboard("{ArrowRight}");
  expect(names()).toEqual(["root", "branch", "leaf", "sibling"]);
  await user.keyboard("{ArrowRight}");
  expect(focused()?.getAttribute("title")).toBe("leaf");
  await user.keyboard("{ArrowLeft}");
  expect(focused()?.getAttribute("title")).toBe("branch");
  await user.keyboard("{Enter}");
  expect(onSelect).toHaveBeenCalledWith("branch");
  await user.keyboard("{End}");
  expect(focused()?.getAttribute("title")).toBe("sibling");
  await user.keyboard(" ");
  expect(onSelect).toHaveBeenCalledWith("sibling");
  await user.keyboard("{Home}");
  expect(focused()?.getAttribute("title")).toBe("root");
});

it("offers no carets, levels or indentation outside the call tree", () => {
  mount(false);
  for (const item of screen.getAllByRole("treeitem")) {
    expect(item.getAttribute("aria-expanded")).toBeNull();
    expect(item.getAttribute("aria-level")).toBe("1");
    expect(item.querySelector("[data-open]")).toBeNull();
  }
  expect(names()).toEqual(["root", "branch", "leaf", "sibling"]);
});
