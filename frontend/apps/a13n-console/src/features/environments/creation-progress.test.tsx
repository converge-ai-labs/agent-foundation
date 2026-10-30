import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CreationProgress, DesktopPreview } from "./creation-progress";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
const access = vi.hoisted(() => ({ write: true }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    can: (verb: string) => verb !== "write" || access.write,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, args: Record<string, unknown> = {}) =>
      key.replace(/{{(\w+)}}/g, (_, name: string) => String(args[name])),
  }),
}));
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  access.write = true;
});
const environment = {
  id: "env_test",
  workspace_id: "ws_test",
  status: "creating",
  created_at: "2026-10-22T10:00:00Z",
  operation_started_at: "2026-10-22T10:00:00Z",
  failure: null,
} as Schema["EnvironmentView"];
function mount(node: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    ...render(<QueryClientProvider client={cache}>{node}</QueryClientProvider>),
    cache,
  };
}

it("uses real build stage and server time across remounts, then retries a confirmed failure with its ETag", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  // Browser time is behind the server; the elapsed display must still show 65 s.
  vi.setSystemTime(new Date("2026-10-22T09:59:00Z"));
  let progress = {
    stage: "building",
    started_at: environment.created_at,
    observed_at: "2026-10-22T10:01:05Z",
    ended_at: null as string | null,
    retryable: false,
  };
  http.GET.mockImplementation(async () => ({ data: progress }));
  http.POST.mockResolvedValue({ data: environment });
  const first = mount(
    <CreationProgress environment={environment} etag='"env_test:2"' />,
  );
  await screen.findByText("Building desktop template in E2B");
  expect(screen.getByText("Elapsed: 1m 5s")).toBeTruthy();
  first.unmount();
  first.cache.clear();
  progress = {
    ...progress,
    stage: "failed",
    ended_at: "2026-10-22T10:01:15Z",
    retryable: true,
  };
  const failed = {
    ...environment,
    failure: {
      code: "auth_failed",
      message: "Check connection credentials",
      permanent: true,
    },
  } as Schema["EnvironmentView"];
  const second = mount(
    <CreationProgress environment={failed} etag='"env_test:3"' />,
  );
  await screen.findByText("Desktop creation needs attention");
  expect(screen.getByText("Elapsed: 1m 15s")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(screen.getByText("Elapsed: 1m 15s")).toBeTruthy();
  await userEvent
    .setup({ advanceTimers: vi.advanceTimersByTime })
    .click(screen.getByRole("button", { name: "Retry creation" }));
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/environments/{environment_id}/retry-creation",
    expect.objectContaining({ headers: { "If-Match": '"env_test:3"' } }),
  );
  second.unmount();
  second.cache.clear();
});

it("does not offer retry before confirmation or without write permission", async () => {
  access.write = false;
  http.GET.mockResolvedValue({
    data: {
      stage: "failed",
      started_at: environment.created_at,
      observed_at: environment.created_at,
      retryable: true,
    },
  });
  const view = mount(<CreationProgress environment={environment} />);
  await screen.findByText("Desktop creation needs attention");
  expect(screen.queryByRole("button", { name: "Retry creation" })).toBeNull();
  view.unmount();
  view.cache.clear();
});

it("captures only on explicit view/refresh and releases preview URLs when closed", async () => {
  const create = vi
    .fn()
    .mockReturnValueOnce("blob:first")
    .mockReturnValueOnce("blob:second");
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  http.GET.mockReset().mockImplementation(async () => ({
    data: new Blob(["image"], { type: "image/png" }),
  }));
  const view = mount(<DesktopPreview environment={environment} />),
    user = userEvent.setup();
  expect(http.GET).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "View desktop" }));
  await screen.findByRole("img", { name: "Desktop preview" });
  await user.click(
    screen.getByRole("button", { name: "Refresh desktop preview" }),
  );
  await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:first"));
  view.unmount();
  view.cache.clear();
  expect(revoke).toHaveBeenCalledWith("blob:second");
  vi.unstubAllGlobals();
});

it("labels Modal build progress without changing the durable progress endpoint", async () => {
  http.GET.mockResolvedValue({
    data: {
      stage: "building",
      started_at: environment.created_at,
      observed_at: environment.created_at,
      retryable: false,
    },
  });
  const view = mount(
    <CreationProgress environment={environment} providerType="modal" />,
  );
  await screen.findByText("Building desktop image in Modal");
  expect(screen.queryByText("Building desktop template in E2B")).toBeNull();
  view.unmount();
  view.cache.clear();
});

it("shows Sprites installation rather than a reusable template build", async () => {
  http.GET.mockResolvedValue({
    data: {
      stage: "building",
      started_at: environment.created_at,
      observed_at: "2026-10-22T10:01:05Z",
      retryable: false,
    },
  });
  const view = mount(
    <CreationProgress environment={environment} providerType="sprites" />,
  );
  await screen.findByText("Installing Envd and desktop dependencies");
  expect(screen.queryByText("Building desktop template in E2B")).toBeNull();
  view.unmount();
  view.cache.clear();
});
