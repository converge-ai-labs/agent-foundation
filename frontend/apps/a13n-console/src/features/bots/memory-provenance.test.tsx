import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryProvenance } from "./memory-provenance";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(cleanup);
it("explains source visibility and keeps received memory read-only", () => {
  render(
    <MemoryProvenance
      document={{
        owner_name: "Product",
        shared: true,
        access_reasons: [{ kind: "installation" }],
        correction_of: null,
      }}
      onOpen={vi.fn()}
    />,
  );
  expect(screen.getByText(/Owning group.*Product/)).toBeTruthy();
  expect(
    screen.getByText(
      "The owning group makes its memory visible to all connected groups.",
    ),
  ).toBeTruthy();
  expect(screen.getByText(/Shared memory is read-only/)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
it("keeps correction navigation on the owning group's document", () => {
  render(
    <MemoryProvenance
      document={{
        owner_name: "Product",
        shared: false,
        access_reasons: [{ kind: "owner" }],
        correction_of: "mdoc_original",
      }}
      onOpen={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("button", { name: "View corrected memory" }),
  ).toBeTruthy();
  expect(screen.getByText("This memory belongs to this group.")).toBeTruthy();
});
