import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { MemorySearch } from "./memory-search";

const http = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("searches only after submission and lets the user narrow to local memory without fetching bodies", async () => {
  http.POST.mockResolvedValue({
    data: {
      items: [{ id: "mdoc_match", title: "Release checklist", shared: false }],
    },
    response: new Response(),
  });
  const select = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <MemorySearch
          accountId="acct_test"
          scopeId="mscope_test"
          onSelect={select}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Search memory" }),
    "release",
  );
  expect(http.POST).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await userEvent.click(
    await screen.findByRole("button", { name: /Release checklist/ }),
  );
  expect(select).toHaveBeenCalledWith("mdoc_match");
  expect(http.POST.mock.calls[0][1].body).toEqual({
    query: "release",
    include_shared: true,
    limit: 20,
  });
  await userEvent.click(screen.getByRole("combobox", { name: "Search range" }));
  await userEvent.click(
    await screen.findByRole("option", { name: "This group's own memory" }),
  );
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(2));
  expect(http.POST.mock.calls[1][1].body.include_shared).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Clear search" }));
  expect(screen.queryByText("Release checklist")).toBeNull();
  expect(http.POST).toHaveBeenCalledTimes(2);
});
