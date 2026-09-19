import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { RunEnvironmentMounts } from "./environment-mounts";

const mocks = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), canAdd: true }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: mocks }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => mocks.canAdd,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../environments/reference", () => ({
  EnvironmentReference: ({ id }: { id: string }) => <span>{id}</span>,
}));
const run = {
  id: "run_test",
  thread_id: "thread_test",
  status: "running",
} as Schema["RunResource"];

beforeEach(() => {
  mocks.canAdd = true;
  mocks.POST.mockReset();
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/threads/")
      ? { current_run_id: "run_test" }
      : path.endsWith("/device")
        ? { default_working_directory: "/default", directory_discovery: false }
        : {
            items: path.endsWith("/environments")
              ? [
                  {
                    id: "env_device",
                    name: "Laptop",
                    device_id: "native",
                  },
                ]
              : [],
          },
  }));
});
function show(value = run) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <RunEnvironmentMounts run={value} />
    </QueryClientProvider>,
  );
  return cache;
}

it("adds the selected Device path and preserves idempotency on a lost acknowledgement", async () => {
  mocks.POST.mockRejectedValueOnce(
    new Error("Connection lost"),
  ).mockResolvedValueOnce({ data: { name: "reference" } });
  const cache = show();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add environment" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", { name: "Laptop (env_device)" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Mount name" }),
    "reference",
  );
  await user.type(
    await screen.findByRole("textbox", { name: "Working directory" }),
    "/projects/docs",
  );
  expect(
    screen.queryByRole("combobox", { name: "Access permissions" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await screen.findByText("Connection lost");
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await waitFor(() => expect(mocks.POST).toHaveBeenCalledTimes(2));
  const request = mocks.POST.mock.calls[0];
  expect(request).toEqual([
    "/api/v1/runs/{run_id}/environment-mounts",
    expect.objectContaining({
      body: {
        name: "reference",
        environment_id: "env_device",
        working_directory: "/projects/docs",
      },
    }),
  ]);
  expect(mocks.POST.mock.calls[1]).toEqual(request);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.clear();
});

it.each(["completed", "waiting"] as const)(
  "does not offer additions for %s Runs",
  async (status) => {
    const cache = show({ ...run, status });
    await screen.findByText("No additional environments.");
    expect(
      screen.queryByRole("button", { name: "Add environment" }),
    ).toBeNull();
    cache.clear();
  },
);

it("requires steering and Environment use permission", async () => {
  mocks.canAdd = false;
  const cache = show();
  await screen.findByText("No additional environments.");
  expect(screen.queryByRole("button", { name: "Add environment" })).toBeNull();
  cache.clear();
});

it("shows accepted loading observations separately from readiness", async () => {
  mocks.GET.mockResolvedValue({
    data: {
      items: [
        {
          name: "reference",
          environment_id: "env_saved",
          working_directory: "/captured",
          application_status: "failed",
          error: { message: "Device is offline" },
        },
      ],
    },
  });
  const cache = show({ ...run, status: "completed" });
  await screen.findByText("/captured");
  expect(screen.getByText("env_saved")).toBeTruthy();
  expect(screen.getByText("Device is offline")).toBeTruthy();
  cache.clear();
});

it("fetches final observations when a Run seals before the next poll", async () => {
  let status = "pending";
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/threads/")
      ? { current_run_id: run.id }
      : {
          items: [
            {
              name: "reference",
              environment_id: "env_saved",
              application_status: status,
            },
          ],
        },
  }));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <RunEnvironmentMounts run={run} />
    </QueryClientProvider>,
  );
  await screen.findByText("state.pending");
  status = "ready";
  view.rerender(
    <QueryClientProvider client={cache}>
      <RunEnvironmentMounts run={{ ...run, status: "completed" }} />
    </QueryClientProvider>,
  );
  await screen.findByText("state.ready");
  expect(screen.queryByText("state.pending")).toBeNull();
  cache.clear();
});
