import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { CreateAgent } from "./create";

const http = vi.hoisted(() => ({ POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
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
        onClick={() => submit({ model: { model_key: "local" }, protocol: {} })}
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

it("uploads a selected avatar after creation and retries without creating another agent", async () => {
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
  const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const value = { id: "agent_new", key: "new-agent", name: "New agent" };
  http.POST.mockResolvedValue({
    data: { agent: value },
    response: new Response(null, { headers: { ETag: '"new"' } }),
  });
  http.PUT.mockRejectedValueOnce(
    new Error("Upload interrupted"),
  ).mockResolvedValueOnce({
    data: { ...value, image_url: "/avatar/new" },
    response: new Response(null, { headers: { ETag: '"image"' } }),
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
      "/workspace/test/agents/new-agent",
    ),
  );
  expect(http.POST).toHaveBeenCalledOnce();
  expect(http.PUT).toHaveBeenCalledTimes(2);
  expect(http.PUT).toHaveBeenLastCalledWith(
    expect.any(String),
    expect.objectContaining({
      body: file,
      params: {
        path: { workspace: "ws_test", agent: "agent_new" },
        header: { "If-Match": '"new"' },
      },
    }),
  );
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:preview");
});
