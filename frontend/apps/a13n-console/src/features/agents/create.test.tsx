import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { CreateAgent } from "./create";

const http = vi.hoisted(() => ({ POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("./editor", () => ({
  AgentEditor: ({
    identity,
    submit,
    pending,
  }: {
    identity: ReactNode;
    submit: (config: object) => void;
    pending: boolean;
  }) => (
    <div>
      {identity}
      <button
        disabled={pending}
        onClick={() => submit({ model: "model-0123456789abcdef0123" })}
      >
        Create agent
      </button>
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

it("creates an agent and navigates to its ID", async () => {
  http.POST.mockResolvedValueOnce({
    data: { id: "ap_new", name: "New agent" },
    response: new Response(null, { headers: { ETag: '"ap_new:1"' } }),
  });
  function Location() {
    return <output aria-label="Current path">{useLocation().pathname}</output>;
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <CreateAgent />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Agent name"), "New agent");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "/workspace/test/agents/ap_new",
    ),
  );
  expect(http.POST).toHaveBeenCalledOnce();
  expect(http.POST.mock.calls[0]?.[1]).toEqual({
    body: {
      name: "New agent",
      description: "",
      config: { model: "model-0123456789abcdef0123" },
    },
  });
});

it("uploads a selected avatar after creation and retries without creating another agent", async () => {
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
  const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const value = { id: "ap_new", name: "New agent" };
  http.POST.mockResolvedValue({
    data: value,
    response: new Response(null, { headers: { ETag: '"ap_new:1"' } }),
  });
  http.PUT.mockRejectedValueOnce(
    new Error("Upload interrupted"),
  ).mockResolvedValueOnce({
    data: { ...value, image_url: "/avatar/new" },
    response: new Response(null, { headers: { ETag: '"ap_new:2"' } }),
  });
  function Location() {
    return <output aria-label="Current path">{useLocation().pathname}</output>;
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const { container, unmount } = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <CreateAgent />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Agent name"), "New agent");
  const file = new File(["png"], "portrait.png", { type: "image/png" });
  await user.upload(
    container.querySelector<HTMLInputElement>('input[type="file"]')!,
    file,
  );
  expect(http.PUT).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await screen.findByText("Upload interrupted");
  expect(http.POST).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Retry upload" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "/workspace/test/agents/ap_new",
    ),
  );
  expect(http.POST).toHaveBeenCalledOnce();
  expect(http.PUT).toHaveBeenCalledTimes(2);
  expect(http.PUT).toHaveBeenLastCalledWith(
    "/api/v1/agents/{agent_id}/avatar",
    {
      params: { path: { agent_id: "ap_new" } },
      headers: { "If-Match": '"ap_new:1"', "Content-Type": "image/png" },
      body: file,
    },
  );
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:preview");
});
