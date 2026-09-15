import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryProvenance } from "./memory-provenance";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("updates the surviving policy explanation without exposing private source controls", () => {
  const document = {
    owner_name: "Engineering",
    shared: true,
    more_access_reasons: false,
    access_reasons: [
      {
        kind: "policy" as const,
        policy_id: "mpol_one",
        policy_name: "Engineering policy",
      },
      {
        kind: "policy" as const,
        policy_id: "mpol_two",
        policy_name: "Support policy",
      },
    ],
    correction_of: null,
    publication_source_id: null,
  };
  const { rerender } = render(
    <MemoryProvenance document={document} onOpen={vi.fn()} />,
  );
  expect(screen.getByText(/Owning group.*Engineering/)).toBeTruthy();
  expect(
    screen.getByText(/Shared through policy.*Engineering policy/),
  ).toBeTruthy();
  rerender(
    <MemoryProvenance
      document={{
        ...document,
        access_reasons: document.access_reasons.slice(1),
      }}
      onOpen={vi.fn()}
    />,
  );
  expect(screen.queryByText(/Engineering policy/)).toBeNull();
  expect(
    screen.getByText(/Shared through policy.*Support policy/),
  ).toBeTruthy();
  expect(screen.getByText(/Shared memory is read-only/)).toBeTruthy();
  expect(screen.queryAllByRole("button")).toHaveLength(0);
});

it("opens authorized correction provenance without offering an edit", async () => {
  const open = vi.fn();
  render(
    <MemoryProvenance
      document={{
        owner_name: "Support",
        shared: false,
        more_access_reasons: false,
        access_reasons: [{ kind: "owner", policy_id: null, policy_name: null }],
        correction_of: "mdoc_previous",
        publication_source_id: null,
      }}
      onOpen={open}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "View corrected memory" }),
  );
  expect(open).toHaveBeenCalledWith("mdoc_previous");
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

it("explains an approved copy without inventing source evidence", () => {
  render(
    <MemoryProvenance
      document={{
        owner_name: "Engineering",
        shared: true,
        more_access_reasons: false,
        access_reasons: [
          { kind: "publication", policy_id: null, policy_name: null },
        ],
        correction_of: null,
        publication_source_id: null,
      }}
      onOpen={vi.fn()}
    />,
  );
  expect(
    screen.getByText("This group received an approved publication."),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "View publication source" }),
  ).toBeNull();
});

it("labels additional grants without claiming its summary is exhaustive", () => {
  render(
    <MemoryProvenance
      document={{
        owner_name: "Engineering",
        shared: true,
        more_access_reasons: true,
        access_reasons: [
          {
            kind: "policy",
            policy_id: "mpol_first",
            policy_name: "First policy",
          },
        ],
        correction_of: null,
        publication_source_id: null,
      }}
      onOpen={vi.fn()}
    />,
  );
  expect(screen.getByText(/Additional policies grant access/)).toBeTruthy();
  expect(screen.queryAllByRole("button")).toHaveLength(0);
});
