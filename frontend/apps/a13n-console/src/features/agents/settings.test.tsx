import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { AgentDetails } from "./settings";

const http = vi.hoisted(() => ({
  PUT: vi.fn(),
  DELETE: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const agent = {
  id: "ap_test",
  name: "Research",
  description: "Finds sources",
  image_url: null,
} as Schema["Agent"];
const response = (data: Schema["Agent"], etag: string) => ({
  data,
  response: new Response(null, { status: 200, headers: { ETag: etag } }),
});

function Location() {
  return <output aria-label="Current path">{useLocation().pathname}</output>;
}

function renderDetails(close = vi.fn(), reload = vi.fn()) {
  const onImageSaved = vi.fn().mockResolvedValue(undefined);
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const { container } = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/test/agents/ap_test"]}>
        <AgentDetails
          close={close}
          resource={{ value: agent, etag: '"initial"' }}
          reload={reload}
          onImageSaved={onImageSaved}
        />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { container, onImageSaved, user: userEvent.setup() };
}

it("preserves metadata drafts across avatar uploads and uses each returned ETag", async () => {
  http.PUT.mockResolvedValue(
    response({ ...agent, image_url: "/avatar/new" }, '"uploaded"'),
  );
  http.DELETE.mockResolvedValue(response(agent, '"removed"'));
  http.PATCH.mockResolvedValue(response(agent, '"saved"'));
  const { container, onImageSaved, user } = renderDetails();
  await user.clear(screen.getByLabelText("Name"));
  await user.type(screen.getByLabelText("Name"), "Draft name");
  const file = new File(["png"], "portrait.png", { type: "image/png" });
  await user.upload(
    container.querySelector<HTMLInputElement>('input[type="file"]')!,
    file,
  );
  await waitFor(() => expect(onImageSaved).toHaveBeenCalledOnce());
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "Draft name",
  );
  expect(http.PUT).toHaveBeenCalledWith("/api/v1/agents/{agent_id}/avatar", {
    params: { path: { agent_id: "ap_test" } },
    headers: { "If-Match": '"initial"', "Content-Type": "image/png" },
    body: file,
  });
  await user.click(screen.getByRole("button", { name: "Remove image" }));
  await waitFor(() => expect(onImageSaved).toHaveBeenCalledTimes(2));
  expect(http.DELETE).toHaveBeenCalledWith("/api/v1/agents/{agent_id}/avatar", {
    params: { path: { agent_id: "ap_test" } },
    headers: { "If-Match": '"uploaded"' },
  });
  // An empty description clears it: the Service reads null as "unchanged".
  await user.clear(screen.getByLabelText("Description"));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith("/api/v1/agents/{agent_id}", {
      params: { path: { agent_id: "ap_test" } },
      headers: { "If-Match": '"removed"' },
      body: { name: "Draft name", description: "" },
    }),
  );
});

it("renames an agent while keeping its ID address", async () => {
  http.PATCH.mockResolvedValue(
    response({ ...agent, name: "Deep research" }, '"saved"'),
  );
  const close = vi.fn(),
    reload = vi.fn();
  const { user } = renderDetails(close, reload);
  const name = screen.getByLabelText("Name");
  await user.clear(name);
  await user.type(name, "Deep research");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0]?.[1].body).toEqual({
    name: "Deep research",
    description: "Finds sources",
  });
  expect(screen.getByLabelText("Current path").textContent).toBe(
    "/workspace/test/agents/ap_test",
  );
  expect(reload).toHaveBeenCalledOnce();
});
