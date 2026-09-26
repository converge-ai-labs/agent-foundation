import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { EnvironmentDetails } from "./instance-details";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  DELETE: vi.fn(),
}));
const access = vi.hoisted(() => ({ can: vi.fn((_verb: string) => true) }));
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
afterEach(() => vi.useRealTimers());

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

const environment: Schema["EnvironmentView"] = {
  id: "env_test",
  name: "Research files",
  organization_id: "org_test",
  workspace_id: "ws_test",
  provider_id: "eprov_test",
  template_id: "envtpl_test",
  device_id: null,
  endpoint: null,
  owner_principal_id: null,
  status: "ready",
  operation_id: null,
  operation_started_at: null,
  failure: null,
  last_used_at: "2026-09-18T00:00:00Z",
  version: 1,
  created_by_id: "usr_test",
  created_at: "2026-09-18T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};
/** An external target: a daemon at its own endpoint, owned by who registered it. */
const external: Schema["EnvironmentView"] = {
  ...environment,
  name: "Work laptop",
  provider_id: null,
  template_id: null,
  device_id: "work-laptop",
  endpoint: "https://laptop.example.com:8443",
  owner_principal_id: "usr_test",
};
const template = {
  id: "envtpl_test",
  config: { recipe: {}, stop_after_seconds: null, delete_after_seconds: null },
};
const provider = { id: "eprov_test", name: "Docker", type: "docker" };
/** Stop and destroy are capabilities of the provider's type. */
function providerType(supports_stop: boolean, supports_destroy: boolean) {
  return { type: provider.type, supports_stop, supports_destroy };
}
const failure = {
  code: "environment_unavailable",
  message: "Provider unreachable",
  certainty: "unknown" as const,
  permanent: false,
  operation_id: "envoper_test",
  at: "2026-09-18T00:00:00Z",
};

/** Reads answer from the current environment; the ETag follows its version. */
function serve(
  current: () => Schema["EnvironmentView"],
  type = providerType(true, true),
) {
  http.GET.mockImplementation(async (path: string) => {
    if (path === "/api/v1/provider-types/{kind}")
      return { data: { items: [type], next_cursor: null } };
    if (path.includes("environment-providers")) return { data: provider };
    if (path.includes("environment-templates")) return { data: template };
    const value = current();
    return {
      data: value,
      response: new Response(null, {
        headers: { ETag: `"env_test:${value.version}"` },
      }),
    };
  });
}

function open(
  value: Schema["EnvironmentView"] = environment,
  user = userEvent.setup(),
) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <EnvironmentDetails environment={value} />
    </QueryClientProvider>,
  );
  return { cache, user };
}

it.each([
  ["stop", "stopping", "stopped"],
  ["delete", "deleting", "deleted"],
  ["stop", "stopping", "failed"],
] as const)(
  "refreshes open details while a %s operation reports %s until %s",
  async (action, phase, outcome) => {
    // Fake time lets the test reach the next 2 s poll without waiting for it.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let current = environment;
    serve(() => current);
    const command = async () => {
      current = {
        ...current,
        status: phase,
        operation_id: "envoper_test",
        version: 2,
      };
      return { data: current };
    };
    http.POST.mockReset().mockImplementation(command);
    http.DELETE.mockReset().mockImplementation(command);
    const { cache, user } = open(
      environment,
      userEvent.setup({ advanceTimers: vi.advanceTimersByTime }),
    );
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("ready");
    await lifecycle(user, action === "stop" ? "Stop target" : "Delete target");
    await user.click(
      screen.getByRole("button", {
        name:
          action === "stop"
            ? "Stop environment target"
            : "Delete environment target",
      }),
    );
    const request = {
      params: { path: { workspace_id: "ws_test", environment_id: "env_test" } },
      headers: { "If-Match": '"env_test:1"' },
    };
    if (action === "stop")
      expect(http.POST).toHaveBeenCalledWith(
        "/api/v1/workspaces/{workspace_id}/environments/{environment_id}/stop",
        request,
      );
    else
      expect(http.DELETE).toHaveBeenCalledWith(
        "/api/v1/workspaces/{workspace_id}/environments/{environment_id}",
        request,
      );
    await screen.findByText(phase);
    expect(screen.getByText("pending")).toBeTruthy();
    expect(screen.getByText("envoper_test")).toBeTruthy();
    current =
      outcome === "failed"
        ? { ...current, failure, version: 3 }
        : { ...current, status: outcome, operation_id: null, version: 3 };
    expect(screen.queryByText(outcome)).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await screen.findByText(outcome);
    cache.clear();
  },
);

it.each([
  ["managed", environment],
  ["external", external],
] as const)(
  "explains %s retention from the template's current idle policy",
  async (ownership, value) => {
    http.GET.mockClear();
    serve(() => value);
    const { cache, user } = open(value);
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("Effective retention policy");
    const templateRead = http.GET.mock.calls.some(([path]) =>
      path.includes("environment-templates"),
    );
    if (ownership === "managed") {
      expect(screen.getAllByText("Disabled")).toHaveLength(2);
      expect(templateRead).toBe(true);
    } else {
      expect(
        screen.getByText(
          "Externally owned: Service does not automatically stop or delete this target.",
        ),
      ).toBeTruthy();
      expect(templateRead).toBe(false);
    }
    cache.clear();
  },
);

it.each([
  [false, true],
  [true, false],
  [false, false],
])(
  "shows only the lifecycle actions a managed environment's provider type supports (stop=%s, destroy=%s)",
  async (supports_stop, supports_destroy) => {
    serve(() => environment, providerType(supports_stop, supports_destroy));
    const { cache, user } = open();
    await user.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("Effective retention policy");
    await waitFor(() =>
      expect(http.GET).toHaveBeenCalledWith(
        "/api/v1/provider-types/{kind}",
        expect.anything(),
      ),
    );
    const actions = supports_stop || supports_destroy;
    if (actions) {
      await user.click(
        await screen.findByRole("button", { name: "Environment actions" }),
      );
      await screen.findByRole("menu");
    } else
      expect(
        screen.queryByRole("button", { name: "Environment actions" }),
      ).toBeNull();
    expect(!!screen.queryByRole("menuitem", { name: "Stop target" })).toBe(
      supports_stop,
    );
    expect(!!screen.queryByRole("menuitem", { name: "Delete target" })).toBe(
      supports_destroy,
    );
    cache.clear();
  },
);

it("shows an external target's endpoint and device, and retires it without stop", async () => {
  http.GET.mockClear();
  serve(() => external, providerType(false, false));
  const { cache, user } = open(external);
  await user.click(screen.getByRole("button", { name: "Details" }));
  expect(await screen.findByText("Device ID")).toBeTruthy();
  expect(screen.getByText("work-laptop")).toBeTruthy();
  expect(screen.getByText("Endpoint")).toBeTruthy();
  expect(screen.getByText("https://laptop.example.com:8443")).toBeTruthy();
  expect(screen.queryByText("Provider")).toBeNull();
  expect(
    http.GET.mock.calls.some(([path]) =>
      path.includes("environment-providers"),
    ),
  ).toBe(false);
  await user.click(
    await screen.findByRole("button", { name: "Environment actions" }),
  );
  await screen.findByRole("menu");
  expect(screen.queryByRole("menuitem", { name: "Stop target" })).toBeNull();
  expect(screen.getByRole("menuitem", { name: "Delete target" })).toBeTruthy();
  cache.clear();
});

it.each([
  ["a new token", "https://laptop.example.com:8443", {}],
  [
    "a new endpoint with its token",
    "https://build-box.example.com",
    { endpoint: "https://build-box.example.com" },
  ],
] as const)(
  "updates an external target's connection with %s",
  async (_change, endpoint, moved) => {
    let current = external;
    serve(() => current);
    http.PATCH.mockReset().mockImplementation(async () => {
      current = { ...current, endpoint, version: 2 };
      return { data: current };
    });
    const { cache, user } = open(external);
    await user.click(screen.getByRole("button", { name: "Details" }));
    await user.click(
      await screen.findByRole("button", { name: "Update connection" }),
    );
    const save = screen.getByRole("button", { name: "Save connection" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    const field = screen.getByLabelText("Endpoint URL");
    await user.clear(field);
    await user.type(field, endpoint);
    await user.type(screen.getByLabelText("Token"), "rotated-token");
    await user.click(save);
    await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
    expect(http.PATCH).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace_id}/environments/{environment_id}",
      {
        params: {
          path: { workspace_id: "ws_test", environment_id: "env_test" },
        },
        headers: { "If-Match": '"env_test:1"' },
        body: { token: "rotated-token", ...moved },
      },
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Save connection" }),
      ).toBeNull(),
    );
    expect(screen.queryByText("rotated-token")).toBeNull();
    cache.clear();
  },
);

it("shows why a lost sandbox cannot be used, and that deleting it is allowed while mounted", async () => {
  const lost: Schema["EnvironmentView"] = {
    ...environment,
    failure: {
      code: "environment_lost",
      message:
        "The sandbox no longer exists at its provider; delete this environment and use a new one",
      certainty: "known",
      permanent: true,
      operation_id: null,
      at: "2026-09-18T00:00:00Z",
    },
  };
  serve(() => lost);
  const { cache, user } = open(lost);
  await user.click(screen.getByRole("button", { name: "Details" }));
  expect(await screen.findByText(lost.failure!.message)).toBeTruthy();
  expect(screen.getByText("Lifecycle command")).toBeTruthy();
  expect(screen.queryByText("Command")).toBeNull();
  await lifecycle(user, "Delete target");
  expect(
    await screen.findByText(
      "The environment is retired and cannot be used again. A managed target is destroyed with its files; a registered device keeps running outside the Service. Environments in use by a run, or mounted by a conversation while still usable, cannot be deleted.",
    ),
  ).toBeTruthy();
  cache.clear();
});

it("hides lifecycle commands and renaming without write access", async () => {
  access.can.mockImplementation((verb: string) => verb !== "write");
  serve(() => environment);
  const { cache, user } = open();
  await user.click(screen.getByRole("button", { name: "Details" }));
  await screen.findByText("Effective retention policy");
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Environment actions" }),
    ).toBeNull(),
  );
  expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
  cache.clear();
});
