import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ThreadComputerSummary } from "./thread-computer-summary";

const mocks = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => mocks }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("shows changed desired permissions and opens configuration without conflating different mounts", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const configure = vi.fn();
  mocks.GET.mockResolvedValue({
    data: {
      items: [
        { name: "workspace", permission_ceiling: { operations: [] } },
        {
          name: "desktop",
          permission_ceiling: { operations: ["environment.computer.observe"] },
        },
      ],
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <ThreadComputerSummary threadId="thr_test" onConfigure={configure} />
    </QueryClientProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", {
      name: "Computer permissions: varies by environment",
    }),
  );
  expect(configure).toHaveBeenCalledOnce();
  mocks.GET.mockResolvedValue({
    data: {
      items: [{ name: "workspace", permission_ceiling: { operations: [] } }],
    },
  });
  await cache.invalidateQueries();
  await screen.findByRole("button", { name: "Computer permissions: off" });
  cache.clear();
});

it("keeps a failed permission read distinct from disabled permissions", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  mocks.GET.mockRejectedValue(new Error("Unavailable"));
  render(
    <QueryClientProvider client={cache}>
      <ThreadComputerSummary threadId="thr_test" onConfigure={() => {}} />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", {
    name: "Computer permissions: unavailable",
  });
  expect(screen.queryByText("Computer permissions: off")).toBeNull();
  cache.clear();
});
