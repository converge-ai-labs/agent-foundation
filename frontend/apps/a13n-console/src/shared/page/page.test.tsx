import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Empty } from "../collection";
import { Page } from "./page";
import { PageActions } from "./page-actions";

it("keeps secondary actions available when an empty collection owns creation", () => {
  const content = (empty: boolean) => (
    <MemoryRouter>
      <Page title="Resources">
        <PageActions secondary>
          <a href="/providers">Manage providers</a>
        </PageActions>
        <PageActions>
          <button>Create resource</button>
        </PageActions>
        {empty ? (
          <Empty
            title="No resources"
            description="Create your first resource."
            action={<button>Create resource</button>}
          />
        ) : (
          <p>Existing resource</p>
        )}
      </Page>
    </MemoryRouter>
  );
  const view = render(content(true));
  expect(
    screen.getAllByRole("button", { name: "Create resource" }),
  ).toHaveLength(1);
  expect(
    screen.getByRole("button", { name: "Create resource" }).closest("header"),
  ).toBeNull();
  expect(
    screen.getByRole("link", { name: "Manage providers" }).closest("header"),
  ).not.toBeNull();
  view.rerender(content(false));
  expect(
    screen.getAllByRole("button", { name: "Create resource" }),
  ).toHaveLength(1);
  expect(
    screen.getByRole("button", { name: "Create resource" }).closest("header"),
  ).not.toBeNull();
});
