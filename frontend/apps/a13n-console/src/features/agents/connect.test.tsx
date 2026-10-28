import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button, Menu, MenuPopup, MenuTrigger } from "a13n-ui";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ConnectAgent } from "./connect";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_design", name: "Design" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
const agent = {
  id: "ap_research",
  name: "Research",
  default_revision_id: "ar_one",
  archived_at: null,
} as Schema["Agent"];
const result = <T,>(data: T) => ({ data, response: new Response() });
const active = { id: "key_active", revoked_at: null, expires_at: null };
const setup = () => {
  const user = userEvent.setup();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <Menu>
        <MenuTrigger render={<Button />}>Agent actions</MenuTrigger>
        <MenuPopup>
          <ConnectAgent agent={agent} />
        </MenuPopup>
      </Menu>
    </QueryClientProvider>,
  );
  const open = async () => {
    await user.click(screen.getByRole("button", { name: "Agent actions" }));
    await user.click(
      await screen.findByRole("menuitem", { name: "Connect to app" }),
    );
    await screen.findByRole("dialog");
  };
  return { user, cache, open };
};
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("creates a key inside the guide, retains failures, blocks pending dismissal and clears the secret on close", async () => {
  http.GET.mockResolvedValue(
    result({
      items: [
        { ...active, id: "key_expired", expires_at: "2020-01-01T00:00:00Z" },
        { ...active, id: "key_revoked", revoked_at: "2020-01-01T00:00:00Z" },
      ],
      next_cursor: null,
    }),
  );
  http.POST.mockRejectedValueOnce(new Error("Creation unavailable"));
  const { user, cache, open } = setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  await open();
  expect(http.POST).not.toHaveBeenCalled();
  expect(screen.queryByRole("textbox")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Create key" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(1));
  await screen.findByText("Creation unavailable");
  let complete!: (value: unknown) => void;
  http.POST.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await user.click(screen.getByRole("button", { name: "Create key" }));
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  http.GET.mockResolvedValue(result({ items: [active], next_cursor: null }));
  complete(result({ secret: "a13n_one_time_test_secret" }));
  await screen.findByText("a13n_one_time_test_secret");
  expect(http.POST).toHaveBeenLastCalledWith("/api/v1/users/me/keys", {
    body: {
      name: expect.stringMatching(/^auto-app-[0-9a-f]{8}$/),
      expires_at: null,
      workspace_id: "ws_design",
    },
  });
  expect(screen.getByRole("link", { name: "Docs" }).getAttribute("href")).toBe(
    "https://a13n.dev/docs/a13n-service/connect-application/",
  );
  await user.click(screen.getByRole("button", { name: "Copy for agent" }));
  const prompt = copy.mock.calls.at(-1)![0];
  expect(prompt).toContain('"agentId": "ap_research"');
  expect(prompt).toContain('"workspaceId": "ws_design"');
  expect(prompt).toContain(window.location.origin);
  expect(prompt).toContain("A13N_API_KEY");
  expect(prompt).not.toContain("a13n_one_time_test_secret");
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain("a13n_one_time_test_secret");
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state.data),
    ),
  ).not.toContain("a13n_one_time_test_secret");
  await user.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await open();
  expect(screen.queryByText("a13n_one_time_test_secret")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  copy.mockRestore();
});

it("finds an existing active key beyond the first page and offers optional inline creation", async () => {
  http.GET.mockImplementation(async (_path, options) =>
    result(
      options.params.query.cursor
        ? { items: [active], next_cursor: null }
        : {
            items: [{ ...active, revoked_at: "2020-01-01T00:00:00Z" }],
            next_cursor: "next",
          },
    ),
  );
  http.POST.mockResolvedValue(
    result({ secret: "a13n_additional_test_secret" }),
  );
  const { user, open } = setup();
  await open();
  await waitFor(() => expect(http.GET).toHaveBeenCalledTimes(2));
  expect(http.GET).toHaveBeenLastCalledWith(
    "/api/v1/users/me/keys",
    expect.objectContaining({
      params: {
        query: { workspace_id: "ws_design", cursor: "next", limit: 100 },
      },
    }),
  );
  expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Create key" }));
  await screen.findByText("a13n_additional_test_secret");
  expect(http.POST).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("does not call a failed key lookup an empty account and can retry without leaving the guide", async () => {
  http.GET.mockRejectedValueOnce(new Error("Keys unavailable"));
  const { user, open } = setup();
  await open();
  await screen.findByText("Keys unavailable");
  expect(screen.queryByText(/No active personal key/)).toBeNull();
  http.GET.mockResolvedValue(result({ items: [], next_cursor: null }));
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByText("No active personal key. Create one to get started.");
  expect(http.POST).not.toHaveBeenCalled();
});
