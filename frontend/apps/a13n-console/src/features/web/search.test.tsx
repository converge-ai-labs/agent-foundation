// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { ApiError } from "../../service-client";
import { WebProviderEditor } from "./editor";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", key: "research" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const scope = { kind: "workspace", id: "ws_test" } as const;
const provider = {
  id: "sp_test",
  name: "Research",
  type: "brave",
  enabled: true,
  workspace_id: "ws_test",
  organization_id: "org_test",
  credential_configured: true,
  configuration: {},
  version: 1,
};
const definition = {
  type: "brave",
  display_name: "Brave Search",
  setup_url: "https://api-dashboard.search.brave.com/",
  configuration_schema: {},
  credential_schema: {
    type: "object",
    properties: { api_key: { type: "string", writeOnly: true } },
    writeOnly: true,
  },
  credential_required: true,
};
const response = (data: unknown, status = 200, etag = '"v1"') => ({
  data,
  response: new Response(null, { status, headers: { ETag: etag } }),
});
function setup(child: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(<QueryClientProvider client={cache}>{child}</QueryClientProvider>);
  return cache;
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
  http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("web-provider-types")
        ? { items: [definition] }
        : path.endsWith("{provider_id}")
          ? provider
          : { items: [provider], next_cursor: null },
    ),
  );
  http.POST.mockResolvedValue(response(provider, 201));
  http.PATCH.mockResolvedValue(response(provider));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates a saved provider independently and clears its credential on close without caching mutation input", async () => {
  const user = userEvent.setup(),
    saved = vi.fn(),
    cache = setup(<WebProviderEditor scope={scope} onSaved={saved} />);
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "Research",
  );
  await user.type(screen.getByLabelText("API Key"), "test-secret");
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(http.POST).toHaveBeenCalledOnce();
  expect(http.POST.mock.calls[0][1].body.credential).toEqual({
    api_key: "test-secret",
  });
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((item) => item.state),
    ),
  ).not.toContain("test-secret");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  expect(await screen.findByLabelText("API Key")).toHaveProperty("value", "");
});

it("keeps the existing credential write-only and sends If-Match for edits", async () => {
  const user = userEvent.setup();
  setup(<WebProviderEditor scope={scope} providerId={provider.id} />);
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("API Key")).toHaveProperty("value", "");
  await user.type(screen.getByRole("textbox", { name: "Name" }), " renamed");
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0][1].body).not.toHaveProperty("credential");
  expect(http.PATCH.mock.calls[0][1].body.enabled).toBe(false);
  expect(http.PATCH.mock.calls[0][1].params.header).toEqual({
    "If-Match": '"v1"',
  });
});

it("reconciles an uncertain create before allowing another attempt", async () => {
  const user = userEvent.setup();
  setup(<WebProviderEditor scope={scope} />);
  http.POST.mockRejectedValue(new TypeError("Network unavailable"));
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "Research",
  );
  await user.type(screen.getByLabelText("API Key"), "test-secret");
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await screen.findByRole("button", { name: "Use this provider" });
  expect(
    http.GET.mock.calls.some(([path]) => path.endsWith("web-providers")),
  ).toBe(true);
  expect(http.POST).toHaveBeenCalledOnce();
});

it("retains the provider draft across a stale ETag and requires loading the current version before resubmitting", async () => {
  const user = userEvent.setup();
  setup(<WebProviderEditor scope={scope} providerId={provider.id} />);
  http.PATCH.mockRejectedValueOnce(
    new ApiError(412, "precondition_failed", "Changed", {}, "req_test"),
  );
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    " draft",
  );
  await user.type(screen.getByLabelText("API Key"), "replacement-secret");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  const reload = await screen.findByRole("button", {
    name: "Load current version and keep my draft",
  });
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveProperty(
    "value",
    "Research draft",
  );
  http.GET.mockImplementationOnce(async () =>
    response({ ...provider, name: "Concurrent name" }, 200, '\"v2\"'),
  );
  await user.click(reload);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", {
        name: "Load current version and keep my draft",
      }),
    ).toBeNull(),
  );
  expect(screen.getByLabelText("API Key")).toHaveProperty(
    "value",
    "replacement-secret",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(2));
  expect(http.PATCH.mock.calls[1][1].params.header).toEqual({
    "If-Match": '\"v2\"',
  });
  expect(http.PATCH.mock.calls[1][1].body).toMatchObject({
    name: "Research draft",
    credential: { api_key: "replacement-secret" },
  });
});
