import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { EnvironmentDetails } from "./instance-details";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
const access = vi.hoisted(() => ({ can: vi.fn((_action: string) => true) }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: access.can }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

beforeEach(() => {
  access.can.mockImplementation(() => true);
});
const capabilities = { supports_stop: true, supports_destroy: true };

/** Lifecycle commands live in the panel's overflow menu. */
async function lifecycle(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  if (!screen.queryByRole("menuitem", { name }))
    await user.click(
      screen.getByRole("button", { name: "Environment actions" }),
    );
  await user.click(await screen.findByRole("menuitem", { name }));
}

const environment: Schema["Environment"] = {
  id: "env_test",
  name: "Research files",
  organization_id: "org_test",
  workspace_id: "ws_test",
  provider_id: "envp_test",
  template_revision_id: "envrev_test",
  ownership: "managed",

  generation: 1,
  status: "running",
  retention_condition: "idle",
  condition_since: "2026-09-18T00:00:00Z",
  created_at: "2026-09-18T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

it.each([
  ["stop", "completed", "stopped"],
  ["delete", "completed", "deleted"],
  ["stop", "failed", "unavailable"],
] as const)(
  "refreshes open details after a pending %s command becomes %s",
  async (action, outcome, status) => {
    let finished = false;
    let detailReads = 0;
    const receipt = () => ({
      id: "envcmd_test",
      status: finished ? outcome : "pending",
    });
    http.GET.mockImplementation(async (path: string) => {
      if (path.includes("environment-commands")) return { data: receipt() };
      if (path.includes("environment-providers"))
        return {
          data: { id: "envp_test", name: "Local", type: "direct_local" },
        };
      detailReads++;
      return {
        data: {
          ...environment,
          ...capabilities,
          status: finished ? status : "running",
          retention: { idle: { stop_after: 600, delete_after: null } },
        },
        response: new Response(null, { headers: { ETag: '"v1"' } }),
      };
    });
    http.POST.mockImplementation(async () => ({ data: receipt() }));
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={cache}>
        <EnvironmentDetails environment={environment} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("running");
    await lifecycle(user, action === "stop" ? "Stop target" : "Delete target");
    await user.click(
      screen.getByRole("button", {
        name:
          action === "stop"
            ? "Stop environment target"
            : "Delete environment target",
      }),
    );
    await screen.findByText("pending");
    await waitFor(() => expect(detailReads).toBeGreaterThan(1));
    const before = detailReads;
    expect(screen.getByText("running")).toBeTruthy();
    finished = true;
    await screen.findByText(status, {}, { timeout: 4000 });
    expect(detailReads).toBeGreaterThan(before);
    expect(screen.getByText(outcome)).toBeTruthy();
    cache.clear();
  },
);

it.each([
  ["managed", { idle: { stop_after: null, delete_after: null } }],
  ["external", null],
] as const)(
  "explains %s retention without fetching the template",
  async (ownership, retention) => {
    http.GET.mockClear();
    http.GET.mockImplementation(async (path: string) => ({
      data: path.includes("environment-providers")
        ? { id: "envp_test", name: "Local", type: "direct_local" }
        : {
            ...environment,
            ownership,
            retention,
            supports_stop: ownership === "managed",
            supports_destroy: ownership === "managed",
          },
      response: new Response(null, { headers: { ETag: '"v1"' } }),
    }));
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={cache}>
        <EnvironmentDetails environment={{ ...environment, ownership }} />
      </QueryClientProvider>,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("Effective retention policy");
    if (ownership === "managed") {
      expect(screen.getAllByText("Disabled")).toHaveLength(2);
      expect(
        screen.getByText(
          "Frozen at allocation. Later template changes do not affect this environment.",
        ),
      ).toBeTruthy();
    } else {
      expect(
        screen.getByText(
          "Externally owned: Service does not automatically stop or delete this target.",
        ),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: "Environment actions" }),
      ).toBeNull();
    }
    expect(
      http.GET.mock.calls.some(([path]) => path.includes("template")),
    ).toBe(false);
    cache.clear();
  },
);

it.each([false, true])(
  "starts a new acknowledged stop and preserves lost-acknowledgement retry identity (%s)",
  async (loseAcknowledgement) => {
    http.POST.mockClear();
    let status = "running";
    let loseNext = loseAcknowledgement;
    const receipts = new Map<string, { id: string; status: string }>();
    http.POST.mockImplementation(
      async (
        _path: string,
        options: { params: { header: { "Idempotency-Key": string } } },
      ) => {
        const key = options.params.header["Idempotency-Key"];
        if (!receipts.has(key))
          receipts.set(key, {
            id: `envcmd_${receipts.size + 1}`,
            status: "completed",
          });
        status = "stopped";
        if (loseNext) {
          loseNext = false;
          throw new Error("Acknowledgement lost");
        }
        return { data: receipts.get(key) };
      },
    );
    http.GET.mockImplementation(
      async (
        path: string,
        options: { params: { path: { command_id?: string } } },
      ) => {
        if (path.includes("environment-commands"))
          return {
            data: [...receipts.values()].find(
              (receipt) => receipt.id === options.params.path.command_id,
            ),
          };
        if (path.includes("environment-providers"))
          return {
            data: { id: "envp_test", name: "Local", type: "direct_local" },
          };
        return {
          data: {
            ...environment,
            ...capabilities,
            status,
            retention: { idle: { stop_after: null, delete_after: null } },
          },
          response: new Response(null, { headers: { ETag: '"v1"' } }),
        };
      },
    );
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={cache}>
        <EnvironmentDetails environment={environment} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("running");
    await lifecycle(user, "Stop target");
    await user.click(
      screen.getByRole("button", { name: "Stop environment target" }),
    );
    if (loseAcknowledgement) {
      await screen.findByText("Acknowledgement lost");
      await user.click(
        screen.getByRole("button", { name: "Stop environment target" }),
      );
      expect(http.POST.mock.calls[0][1].params.header["Idempotency-Key"]).toBe(
        http.POST.mock.calls[1][1].params.header["Idempotency-Key"],
      );
    }
    await screen.findByText("envcmd_1");
    await screen.findByText("stopped");
    await user.click(
      within(
        screen.getByRole("complementary", { name: "Environment details" }),
      ).getByRole("button", { name: "Close" }),
    );
    // Another Run resumes the same target while this component stays mounted.
    status = "running";
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("running");
    await lifecycle(user, "Stop target");
    await user.click(
      screen.getByRole("button", { name: "Stop environment target" }),
    );
    await screen.findByText("envcmd_2");
    await screen.findByText("stopped");
    expect(receipts.size).toBe(2);
    expect(
      http.POST.mock.calls.at(-1)![1].params.header["Idempotency-Key"],
    ).not.toBe(http.POST.mock.calls[0][1].params.header["Idempotency-Key"]);
    expect(http.POST).toHaveBeenCalledTimes(loseAcknowledgement ? 3 : 2);
    cache.clear();
  },
);

it.each([
  [false, true],
  [true, false],
  [false, false],
])(
  "shows only supported lifecycle actions without provider-read access (stop=%s, destroy=%s)",
  async (supports_stop, supports_destroy) => {
    access.can.mockImplementation(
      (action: string) => action !== "environment_provider.read",
    );
    http.GET.mockClear();
    http.GET.mockResolvedValue({
      data: {
        ...environment,
        supports_stop,
        supports_destroy,
        retention: { idle: { stop_after: null, delete_after: null } },
      },
      response: new Response(null, { headers: { ETag: '"v1"' } }),
    });
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={cache}>
        <EnvironmentDetails environment={environment} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("Effective retention policy");
    const actions = screen.queryByRole("button", {
      name: "Environment actions",
    });
    expect(!!actions).toBe(supports_stop || supports_destroy);
    if (actions) {
      await user.click(actions);
      await screen.findByRole("menu");
    }
    expect(!!screen.queryByRole("menuitem", { name: "Stop target" })).toBe(
      supports_stop,
    );
    expect(!!screen.queryByRole("menuitem", { name: "Delete target" })).toBe(
      supports_destroy,
    );
    expect(
      http.GET.mock.calls.every(
        ([path]) => !path.includes("environment-provider"),
      ),
    ).toBe(true);
    cache.clear();
  },
);
