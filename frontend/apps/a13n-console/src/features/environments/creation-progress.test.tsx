import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  CreationProgress,
  DesktopPreview,
  creationNotice,
  HEADLESS_INSTALL_NOTICE,
  HEADLESS_IMAGE_NOTICE,
  HEADLESS_SNAPSHOT_NOTICE,
  VERCEL_FIRST_DESKTOP_NOTICE,
} from "./creation-progress";
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
  desktop_preview: "supported",
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

it.each([
  [
    "e2b",
    { template: "base", preparation: "headless" },
    HEADLESS_INSTALL_NOTICE,
  ],
  ["modal", { image: "auto" }, HEADLESS_IMAGE_NOTICE],
  ["daytona", { snapshot: "auto" }, HEADLESS_SNAPSHOT_NOTICE],
  ["vercel", {}, VERCEL_FIRST_DESKTOP_NOTICE],
  ["sprites", {}, HEADLESS_INSTALL_NOTICE],
  ["vercel", { snapshot: "snap_custom" }, undefined],
  ["modal", { image: "im_custom" }, undefined],
  ["e2b", { template: "custom", preparation: "preinstalled" }, undefined],
  ["docker", {}, undefined],
] as const)(
  "selects the %s preparation notice from provider and recipe",
  (type, recipe, notice) => {
    expect(creationNotice(type, { execution: "envd_http", ...recipe })).toBe(
      notice,
    );
  },
);

it("omits preparation for an instance whose frozen recipe is not automatic", async () => {
  http.GET.mockResolvedValue({ data: null });
  const view = mount(
    <CreationProgress environment={environment} providerType="modal" />,
  );
  await waitFor(() =>
    expect(
      view.cache.getQueryState(["environment-creation", environment.id])
        ?.status,
    ).toBe("success"),
  );
  expect(view.container.textContent).toBe("");
  view.unmount();
  view.cache.clear();
});

it("uses real build stage and server time across remounts, then retries a confirmed failure with its ETag", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  // Browser time is behind the server; the elapsed display must still show 65 s.
  vi.setSystemTime(new Date("2026-10-22T09:59:00Z"));
  let progress = {
    profile: "desktop",
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
      profile: "desktop",
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
      profile: "desktop",
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
      profile: "desktop",
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

it("disables unsupported instance previews without sending a request", async () => {
  http.GET.mockClear();
  mount(
    <DesktopPreview
      environment={{
        ...environment,
        desktop_preview: "connection_unsupported",
      }}
    />,
  );
  const button = screen.getByRole("button", { name: "View desktop" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(
    screen.getByText(
      "Desktop preview is not supported for this connection mode.",
    ),
  ).toBeTruthy();
  await userEvent.click(button);
  expect(http.GET).not.toHaveBeenCalled();
});

it.each([
  ["vercel", "headless", "Installing headless Envd"],
  ["vercel", "desktop", "Installing Envd and desktop dependencies"],
  ["e2b", "headless", "Installing headless Envd"],
  ["modal", "headless", "Building headless Envd image in Modal"],
  ["daytona", "headless", "Building headless Envd snapshot in Daytona"],
])(
  "renders %s %s preparation from the frozen server profile",
  async (providerType, profile, label) => {
    http.GET.mockResolvedValue({
      data: {
        profile,
        stage: "building",
        started_at: environment.created_at,
        observed_at: environment.created_at,
        retryable: false,
      },
    });
    const view = mount(
      <CreationProgress
        environment={environment}
        providerType={providerType}
      />,
    );
    await screen.findByText(label);
    expect(
      screen.queryByText("Building desktop snapshot in Vercel"),
    ).toBeNull();
    view.unmount();
    view.cache.clear();
  },
);
