import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { AgentDetails } from "./settings";

const http = vi.hoisted(() => ({
  PUT: vi.fn(),
  DELETE: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
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

it("preserves metadata drafts across avatar uploads and uses each returned ETag", async () => {
  const user = userEvent.setup();
  const value = {
    id: "agent_test",
    key: "research",
    name: "Research",
    description: "",
    image_url: null,
  } as Schema["Agent"];
  const response = (data: Schema["Agent"], etag: string) => ({
    data,
    response: new Response(null, { status: 200, headers: { ETag: etag } }),
  });
  http.PUT.mockResolvedValue(
    response({ ...value, image_url: "/avatar/new" }, '"uploaded"'),
  );
  http.DELETE.mockResolvedValue(response(value, '"removed"'));
  http.PATCH.mockResolvedValue(response(value, '"saved"'));
  const onImageSaved = vi.fn().mockResolvedValue(undefined);
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const { container } = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentDetails
          close={vi.fn()}
          resource={{ value, etag: '"initial"' }}
          reload={vi.fn()}
          onImageSaved={onImageSaved}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
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
  expect(http.PUT).toHaveBeenCalledWith(
    expect.stringContaining("/avatar"),
    expect.objectContaining({
      body: file,
      params: expect.objectContaining({ header: { "If-Match": '"initial"' } }),
    }),
  );
  await user.click(screen.getByRole("button", { name: "Remove image" }));
  await waitFor(() => expect(onImageSaved).toHaveBeenCalledTimes(2));
  expect(http.DELETE).toHaveBeenCalledWith(
    expect.stringContaining("/avatar"),
    expect.objectContaining({
      params: expect.objectContaining({ header: { "If-Match": '"uploaded"' } }),
    }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        body: expect.objectContaining({ name: "Draft name" }),
        params: expect.objectContaining({
          header: { "If-Match": '"removed"' },
        }),
      }),
    ),
  );
});
