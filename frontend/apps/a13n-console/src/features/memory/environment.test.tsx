import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fileEntry, MemoryEntryFields } from "./entries";
import { MemoryEnvironmentField } from "./environment";
import type { Schema } from "../../shared/api";

const mocks = vi.hoisted(() => ({ GET: vi.fn(), allowed: true }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: mocks }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => mocks.allowed,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const response = (data: unknown) => ({ data, response: new Response(null) });
const environment = {
  id: "env_project",
  name: "Project workspace",
  status: "running",
};
function setup(child: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(<QueryClientProvider client={client}>{child}</QueryClientProvider>);
}
beforeEach(() => {
  mocks.allowed = true;
  mocks.GET.mockReset();
  mocks.GET.mockImplementation(async () =>
    response({ items: [], next_cursor: null }),
  );
});

it("selects an environment from later pages and clears only the explicit environment when returning to the run default", async () => {
  mocks.GET.mockImplementation(
    async (
      path: string,
      options: { params: { query?: { cursor?: string } } },
    ) => {
      if (!path.endsWith("/environments"))
        return response({ items: [], next_cursor: null });
      return response(
        options.params.query?.cursor
          ? { items: [environment], next_cursor: null }
          : {
              items: [
                { ...environment, id: "env_other", name: "Other workspace" },
              ],
              next_cursor: "next",
            },
      );
    },
  );
  const changed = vi.fn();
  function Form() {
    const [entry, setEntry] = useState<Schema["MemoryEntrySelection"]>({
      ...fileEntry(),
      backend: {
        type: "filesystem",
        configuration: { storage: { root: "/notes" } },
      },
    });
    return (
      <MemoryEntryFields
        entry={entry}
        preset
        onChange={(next) => {
          changed(next);
          setEntry(next);
        }}
      />
    );
  }
  const user = userEvent.setup();
  setup(<Form />);
  await waitFor(() =>
    expect(screen.queryByText("Loading environments…")).toBeNull(),
  );
  expect(
    screen.getByRole("combobox", { name: "Memory environment" }).textContent,
  ).toBe("Current run environment");
  await user.click(
    screen.getByRole("combobox", { name: "Memory environment" }),
  );
  await user.click(
    await screen.findByRole("option", { name: /Project workspace/ }),
  );
  expect(changed.mock.lastCall?.[0].backend.configuration.storage).toEqual({
    root: "/notes",
    environment_id: "env_project",
  });
  await user.click(
    screen.getByRole("combobox", { name: "Memory environment" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Current run environment" }),
  );
  expect(changed.mock.lastCall?.[0].backend.configuration.storage).toEqual({
    root: "/notes",
  });
  expect(
    screen.getByRole("combobox", { name: "Memory environment" }).textContent,
  ).toBe("Current run environment");
});

it("preserves the saved selection through a failed load and supports retry", async () => {
  mocks.GET.mockRejectedValueOnce(new Error("Network failure"));
  const change = vi.fn();
  const user = userEvent.setup();
  setup(
    <MemoryEnvironmentField
      value="env_saved"
      onChange={change}
      readOnly={false}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Reload" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull(),
  );
  expect(
    screen.getByRole("combobox", { name: "Memory environment" }).textContent,
  ).toContain("env_saved");
  expect(change).not.toHaveBeenCalled();
});

it("does not request environments without permission and preserves the saved reference", () => {
  mocks.allowed = false;
  const change = vi.fn();
  setup(
    <MemoryEnvironmentField
      value="env_saved"
      onChange={change}
      readOnly={false}
    />,
  );
  expect(screen.getByText(/You do not have permission/)).toBeTruthy();
  expect(screen.getByRole("combobox").textContent).toContain("env_saved");
  expect(mocks.GET).not.toHaveBeenCalled();
  expect(change).not.toHaveBeenCalled();
});

it("labels unavailable environments without allowing new selection", async () => {
  mocks.GET.mockResolvedValue(
    response({
      items: [{ ...environment, status: "unavailable" }],
      next_cursor: null,
    }),
  );
  const user = userEvent.setup();
  setup(
    <MemoryEnvironmentField value="" onChange={vi.fn()} readOnly={false} />,
  );
  await waitFor(() =>
    expect(screen.queryByText("Loading environments…")).toBeNull(),
  );
  await user.click(screen.getByRole("combobox"));
  expect(
    (
      await screen.findByRole("option", { name: /Project workspace/ })
    ).getAttribute("aria-disabled"),
  ).toBe("true");
});
