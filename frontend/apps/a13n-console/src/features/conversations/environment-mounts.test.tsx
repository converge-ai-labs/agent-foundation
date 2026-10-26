import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ThreadEnvironmentMounts } from "./environment-mounts";
import { fixtureSession, fixtureThread } from "./transcript/fixture";

const mocks = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
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

beforeEach(() => {
  mocks.canAdd = true;
  mocks.thread = fixtureThread();
  mocks.mounts = [];
  mocks.POST.mockReset();
  mocks.PATCH.mockReset();
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/sessions/{session_id}")
      ? fixtureSession()
      : path.endsWith("/threads/{thread_id}")
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
      <ThreadEnvironmentMounts thread={fixtureThread()} />
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

it("mounts a managed instance at a directory and clears stale paths on selection changes", async () => {
  mocks.POST.mockResolvedValueOnce({ data: { name: "project" } });
  const cache = show();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add environment" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", { name: "Sandbox (env_sandbox)" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Working directory" }),
    "/projects/a",
  );
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", { name: "Laptop (env_device)" }),
  );
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("");
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", { name: "Sandbox (env_sandbox)" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Working directory" }),
    "/projects/b",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Mount name" }),
    "project",
  );
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await waitFor(() =>
    expect(mocks.POST).toHaveBeenCalledWith(
      "/api/v1/threads/{thread_id}/environments",
      expect.objectContaining({
        body: {
          name: "project",
          environment_id: "env_sandbox",
          working_directory: "/projects/b",
        },
      }),
    ),
  );
  cache.clear();
});

it("does not offer additions on a child Thread", async () => {
  mocks.thread = fixtureThread({ origin: "child" });
  const cache = show();
  await screen.findByText("No environments mounted.");
  expect(screen.queryByRole("button", { name: "Add environment" })).toBeNull();
  cache.clear();
});

it("requires run permission", async () => {
  mocks.canAdd = false;
  const cache = show();
  await screen.findByText("No environments mounted.");
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

it.each(["observation", "interaction"])(
  "changes only Computer permissions to %s on the primary mount and uses the Thread version",
  async (mode) => {
    mocks.mounts = [
      {
        name: "workspace",
        environment_id: "env_saved",
        working_directory: null,
        permission_ceiling: {
          operations: [
            "environment.file.read_text",
            "environment.computer.observe",
          ],
        },
      },
    ];
    mocks.PATCH.mockResolvedValue({ data: mocks.mounts[0] });
    const cache = show(),
      user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: "Computer permissions: custom",
      }),
    );
    expect(screen.getByText(/Changes apply to later runs/)).toBeTruthy();
    await user.click(
      screen.getByRole("combobox", { name: "Computer permissions" }),
    );
    await user.click(
      await screen.findByRole("option", {
        name: `Computer permissions: allow ${mode}`,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mocks.PATCH).toHaveBeenCalledWith(
        "/api/v1/threads/{thread_id}/environments/{name}",
        expect.objectContaining({
          params: { path: { thread_id: "thr_1", name: "workspace" } },
          headers: { "If-Match": '"thr_1:4"' },
          body: {
            permission_ceiling: {
              operations: [
                "environment.file.read_text",
                "environment.computer.describe",
                "environment.computer.observe",
                "environment.computer.zoom",
                ...(mode === "interaction"
                  ? [
                      "environment.computer.click",
                      "environment.computer.move",
                      "environment.computer.drag",
                      "environment.computer.scroll",
                      "environment.computer.type_text",
                      "environment.computer.press_keys",
                      "environment.computer.read_clipboard",
                      "environment.computer.write_clipboard",
                    ]
                  : []),
              ],
            },
          },
        }),
      ),
    );
    cache.clear();
  },
);

it("shows Computer permissions without offering edits to a reader", async () => {
  mocks.canAdd = false;
  mocks.mounts = [
    {
      name: "workspace",
      environment_id: "env_saved",
      working_directory: null,
      permission_ceiling: {
        operations: [
          "environment.computer.describe",
          "environment.computer.observe",
          "environment.computer.zoom",
        ],
      },
    },
  ];
  const cache = show();
  await screen.findByText("Computer permissions: allow observation");
  expect(screen.queryByRole("button", { name: "Check desktop" })).toBeNull();
  expect(mocks.POST).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", {
      name: "Computer permissions: allow observation",
    }),
  ).toBeNull();
  cache.clear();
});

it("checks actual desktop support without changing mount permissions", async () => {
  mocks.mounts = [
    {
      name: "workspace",
      environment_id: "env_device",
      working_directory: null,
      permission_ceiling: { operations: ["environment.computer.observe"] },
    },
  ];
  mocks.POST.mockResolvedValueOnce({
    data: { status: "unsupported", operations: [] },
  });
  const cache = show();
  const user = userEvent.setup();
  await screen.findByText("Not checked", { exact: false });
  expect(mocks.POST).not.toHaveBeenCalled();
  await user.click(
    await screen.findByRole("button", { name: "Check desktop" }),
  );
  await screen.findByText(
    "This environment does not provide desktop operations.",
  );
  expect(mocks.POST).toHaveBeenCalledWith(
    "/api/v1/environments/{environment_id}/computer/check",
    {
      params: { path: { environment_id: "env_device" } },
    },
  );
  expect(
    screen.getByText("Allowing Computer actions does not add desktop support."),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Computer permissions: custom" }),
  ).toBeTruthy();
  expect(mocks.PATCH).not.toHaveBeenCalled();
  cache.clear();
});

it("rechecks readiness without retaining stale success after a failed check", async () => {
  mocks.mounts = [
    {
      name: "workspace",
      environment_id: "env_device",
      working_directory: null,
    },
  ];
  mocks.POST.mockResolvedValueOnce({
    data: {
      status: "checked",
      observe_ready: true,
      input_ready: true,
      display_count: 1,
      operations: [],
    },
  }).mockRejectedValueOnce(new Error("Desktop connection lost"));
  const cache = show();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Check desktop" }),
  );
  await screen.findByText("Desktop ready for observation and input.");
  // A ready desktop does not grant the Agent Computer permissions.
  expect(
    screen.getByRole("button", { name: "Computer permissions: off" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Check again" }));
  await screen.findByText("Desktop connection lost");
  expect(screen.getByRole("status").textContent).toContain("Check failed");
  expect(
    screen.queryByText("Desktop ready for observation and input."),
  ).toBeNull();
  expect(
    screen.queryByText("This environment does not provide desktop operations."),
  ).toBeNull();
  expect(mocks.PATCH).not.toHaveBeenCalled();
  cache.clear();
});
