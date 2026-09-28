import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { RunEnvironmentMounts } from "./environment-mounts";
import { fixtureRun, fixtureThread } from "./transcript/fixture";

const mocks = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  canAdd: true,
  thread: {} as Schema["ThreadView"],
  mounts: [] as Schema["MountView"][],
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: mocks, workspace: () => mocks }),
}));
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
const run = fixtureRun({ status: "completed" });

beforeEach(() => {
  mocks.canAdd = true;
  mocks.thread = fixtureThread();
  mocks.mounts = [];
  mocks.POST.mockReset();
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/threads/{thread_id}")
      ? mocks.thread
      : path.endsWith("/threads/{thread_id}/environments")
        ? { items: mocks.mounts }
        : {
            items: [
              { id: "env_device", name: "Laptop", template_id: null },
              { id: "env_sandbox", name: "Sandbox", template_id: "tpl_1" },
            ],
            next_cursor: null,
          },
  }));
});
function show() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <RunEnvironmentMounts run={run} />
    </QueryClientProvider>,
  );
  return cache;
}

it("mounts an external target's working directory on the Thread it was read with, retrying the same request", async () => {
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
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await screen.findByText("Connection lost");
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await waitFor(() => expect(mocks.POST).toHaveBeenCalledTimes(2));
  const request = mocks.POST.mock.calls[0];
  expect(request).toEqual([
    "/api/v1/threads/{thread_id}/environments",
    expect.objectContaining({
      params: { path: { thread_id: "thr_1" } },
      headers: { "If-Match": '"thr_1:4"' },
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

it("asks for a working directory only on an external target", async () => {
  const cache = show();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add environment" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", { name: "Sandbox (env_sandbox)" }),
  );
  expect(
    screen.queryByRole("textbox", { name: "Working directory" }),
  ).toBeNull();
  cache.clear();
});

it("does not offer additions on a child Thread", async () => {
  mocks.thread = fixtureThread({ origin: "child" });
  const cache = show();
  await screen.findByText("No additional environments.");
  expect(screen.queryByRole("button", { name: "Add environment" })).toBeNull();
  cache.clear();
});

it("requires run permission", async () => {
  mocks.canAdd = false;
  const cache = show();
  await screen.findByText("No additional environments.");
  expect(screen.queryByRole("button", { name: "Add environment" })).toBeNull();
  cache.clear();
});

it("lists the Thread's mounts with their working directories", async () => {
  mocks.mounts = [
    {
      name: "reference",
      environment_id: "env_saved",
      working_directory: "/captured",
    },
  ];
  const cache = show();
  await screen.findByText("/captured");
  expect(screen.getByText("reference")).toBeTruthy();
  expect(screen.getByText("env_saved")).toBeTruthy();
  cache.clear();
});
