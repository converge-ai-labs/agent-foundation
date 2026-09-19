import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { ConnectDevice } from "./device-pairing";
import { pairingSearch } from "./pairing-link";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
const catalog = vi.hoisted(() => ({ supported: true }));
const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router", async (original) => ({
  ...(await original<typeof import("react-router")>()),
  useNavigate: () => navigate,
}));
vi.mock("./providers", () => ({
  useEnvironmentTypes: () => ({
    data: { items: catalog.supported ? [{ type: "websocket_envd" }] : [] },
    isPending: false,
    error: null,
  }),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", name: "Research", key: "research" },
    workspaces: [
      { id: "ws_test", name: "Research", key: "research" },
      { id: "ws_other", name: "Personal", key: "personal" },
    ],
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const pairing = `pair-${"a".repeat(24)}`;
const challenge = {
  pairing_id: pairing,
  verification_code: "ABCD-1234",
  device_id: "device_work",
  name: "Work laptop",
  expires_at: "2099-09-19T00:00:00Z",
};
beforeEach(() => {
  catalog.supported = true;
  navigate.mockReset();
  http.GET.mockReset().mockResolvedValue({ data: challenge });
  http.POST.mockReset().mockResolvedValue({
    data: { id: "env_device", workspace_id: "ws_test" },
  });
});
function show(search = "") {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const approved = vi.fn();
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[`/${search}`]}>
        <ConnectDevice onApproved={approved} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { approved, cache, user: userEvent.setup() };
}

it("reviews the terminal link and approves only in the explicitly selected Workspace", async () => {
  http.POST.mockResolvedValue({
    data: { id: "env_device", workspace_id: "ws_other" },
  });
  const { approved, cache, user } = show(`?envd_pairing=${pairing}`);
  expect(await screen.findByText("ABCD-1234")).toBeTruthy();
  expect(screen.getByText("Work laptop")).toBeTruthy();
  expect(http.POST).not.toHaveBeenCalled();
  screen.getByRole("combobox", { name: "Workspace" }).focus();
  await user.keyboard("{ArrowDown}");
  await user.click(await screen.findByRole("option", { name: "Personal" }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      "/api/v1/workspaces/{workspace}/device-pairings/{pairing_id}",
      expect.objectContaining({
        params: { path: { workspace: "ws_other", pairing_id: pairing } },
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Approve device" }));
  await waitFor(() => expect(approved).toHaveBeenCalledOnce());
  expect(navigate).toHaveBeenCalledWith(
    "/workspace/personal/environments/instances",
  );
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/device-pairings/{pairing_id}/approve",
    { params: { path: { workspace: "ws_other", pairing_id: pairing } } },
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.clear();
});

it("copies a single-instance command and reviews a pasted same-Host link", async () => {
  const { cache, user } = show();
  await user.click(screen.getByRole("button", { name: "Connect device" }));
  expect(
    screen.getByText(/a13n-envd connect .* --host service --instance service/),
  ).toBeTruthy();
  expect(http.GET).not.toHaveBeenCalled();
  await user.type(
    screen.getByRole("textbox", { name: "Pairing link" }),
    `https://other.example/?envd_pairing=${pairing}`,
  );
  await user.click(screen.getByRole("button", { name: "Review device" }));
  expect(
    await screen.findByText(
      "Paste the pairing link printed by envd for this Host.",
    ),
  ).toBeTruthy();
  expect(http.GET).not.toHaveBeenCalled();
  await user.clear(screen.getByRole("textbox", { name: "Pairing link" }));
  await user.type(
    screen.getByRole("textbox", { name: "Pairing link" }),
    `${window.location.origin}/?envd_pairing=${pairing}`,
  );
  await user.click(screen.getByRole("button", { name: "Review device" }));
  expect(await screen.findByText("ABCD-1234")).toBeTruthy();
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  await user.click(screen.getByRole("button", { name: "Reject device" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.clear();
});

it("keeps the challenge open when the Service rejects management authority", async () => {
  http.POST.mockRejectedValue(new Error("Workspace access was removed."));
  const { cache, user } = show(`?envd_pairing=${pairing}`);
  await screen.findByText("ABCD-1234");
  await user.click(screen.getByRole("button", { name: "Reject device" }));
  expect(await screen.findByText("Workspace access was removed.")).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(navigate).not.toHaveBeenCalled();
  cache.clear();
});

it("explains unsupported deployments without offering a command or approval", async () => {
  catalog.supported = false;
  const { cache } = show(`?envd_pairing=${pairing}`);
  expect(
    await screen.findByText(/Device connections are unavailable/),
  ).toBeTruthy();
  expect(screen.queryByText(/a13n-envd connect/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve device" })).toBeNull();
  expect(http.GET).not.toHaveBeenCalled();
  cache.clear();
});

it("preserves only a valid pairing identity through authentication redirects", () => {
  expect(pairingSearch(`?envd_pairing=${pairing}&token=do-not-forward`)).toBe(
    `?envd_pairing=${pairing}`,
  );
  expect(
    pairingSearch("?envd_pairing=invalid&returnTo=https://other.example"),
  ).toBe("");
});
