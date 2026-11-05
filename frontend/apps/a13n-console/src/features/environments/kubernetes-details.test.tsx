import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { KubernetesDetails } from "./kubernetes-details";

const http = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn() }));
const access = vi.hoisted(() => ({ can: vi.fn((_verb: string) => true) }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ can: access.can }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const environment: Schema["EnvironmentView"] = {
  id: "env_test",
  name: "Kubernetes sandbox",
  organization_id: "org_test",
  workspace_id: "ws_test",
  provider_id: "eprov_test",
  template_id: "envt_test",
  device_id: null,
  endpoint: null,
  owner_principal_id: null,
  status: "creating",
  operation_id: "envoper_test",
  operation_started_at: "2026-11-04T00:00:00Z",
  failure: null,
  last_used_at: null,
  version: 1,
  created_by_id: "usr_test",
  created_at: "2026-11-04T00:00:00Z",
  updated_at: "2026-11-04T00:00:00Z",
};
const native = {
  observed_at: "2026-11-04T01:00:00Z",
  namespace: "sandbox",
  pod_name: "a13n-test",
  pod_uid: "uid-exact",
  stage: "pending",
  connection: "port_forward",
  envd: "not_verified",
  errors: [],
  resources: [
    {
      reference: {
        kind: "Pod",
        name: "a13n-test",
        uid: "uid-exact",
        disposition: "delete",
      },
      observation: "present",
      finalizers: ["example.com/cleanup"],
      status: { phase: "Pending" },
    },
  ],
  events: [{ reason: "FailedScheduling", message: "Insufficient cpu" }],
  events_truncated: false,
  rendered: { pod: { spec: {} } },
  commands: ["kubectl -n sandbox describe pod a13n-test"],
};
function mount(value = environment) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    cache,
    ...render(
      <QueryClientProvider client={cache}>
        <KubernetesDetails environment={value} etag='"1"' />
      </QueryClientProvider>,
    ),
  };
}
beforeEach(() => {
  access.can.mockImplementation(() => true);
  http.GET.mockImplementation(async (path: string) => ({
    response: new Response(),
    data: path.endsWith("/logs")
      ? { container: "envd", pod_uid: "uid-exact", text: "startup failed" }
      : { native },
  }));
  http.PUT.mockResolvedValue({ response: new Response(), data: environment });
});

it("inspects only on request, preserves native identities, and reads bounded logs explicitly", async () => {
  const user = userEvent.setup();
  mount();
  expect(http.GET).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Inspect Kubernetes" }));
  expect(await screen.findByText("a13n-test")).toBeTruthy();
  expect(http.GET).toHaveBeenCalledTimes(1);
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  expect(http.GET).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Kubernetes events" }));
  expect(screen.getByText("Insufficient cpu")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Native resources" }));
  expect(screen.getByText(/example.com\/cleanup/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Container logs" }));
  await user.type(
    screen.getByRole("textbox", { name: "Container name (optional)" }),
    "sidecar",
  );
  await user.click(screen.getByRole("switch", { name: "Previous container" }));
  await user.click(screen.getByRole("button", { name: "Read last 100 lines" }));
  expect(await screen.findByText("startup failed")).toBeTruthy();
  expect(http.GET).toHaveBeenLastCalledWith(
    "/api/v1/environments/{environment_id}/kubernetes/logs",
    {
      params: {
        path: { environment_id: "env_test" },
        query: { container: "sidecar", previous: true, tail_lines: 100 },
      },
    },
  );
  expect(http.PUT).not.toHaveBeenCalled();
});

it("uses the instance precondition for a finite hold and surfaces conflicts", async () => {
  const user = userEvent.setup();
  http.PUT.mockResolvedValueOnce({
    response: new Response(null, { status: 412 }),
    error: { detail: "Version conflict" },
  });
  const { cache } = mount();
  const invalidate = vi.spyOn(cache, "invalidateQueries");
  await user.click(screen.getByRole("button", { name: "Hold for 1 hour" }));
  await waitFor(() =>
    expect(http.PUT).toHaveBeenCalledWith(
      "/api/v1/environments/{environment_id}/debug-hold",
      {
        params: { path: { environment_id: "env_test" } },
        headers: { "If-Match": '"1"' },
        body: { seconds: 3600 },
      },
    ),
  );
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(invalidate).not.toHaveBeenCalled();
  expect(http.GET).not.toHaveBeenCalled();
});

it("withholds diagnostics and hold mutation from read-only viewers", () => {
  access.can.mockImplementation(() => false);
  mount();
  expect(
    screen.queryByRole("button", { name: "Inspect Kubernetes" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Hold for 1 hour" })).toBeNull();
  expect(http.GET).not.toHaveBeenCalled();
});

it("keeps retained storage discoverable after deletion without native reads", async () => {
  mount({
    ...environment,
    status: "deleted",
    retirement_state: {
      provider_key: "kubernetes",
      state_version: "1",
      state: {
        resources: [
          {
            kind: "PersistentVolumeClaim",
            name: "retained-data",
            uid: "pvc-uid",
            disposition: "retain",
          },
        ],
      },
    },
  });
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Retained resource receipt" }));
  expect(screen.getByText(/"retained-data"/).textContent).toContain("pvc-uid");
  expect(http.GET).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Hold for 1 hour" })).toBeNull();
});
