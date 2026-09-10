import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router";
import { Profile } from "./profile";

const http = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../layout/shell", () => ({
  Avatar: ({ name }: { name: string }) => <span>{name}</span>,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(
  editable = true,
  kind: "workspace" | "organization" = "workspace",
) {
  const resource = {
    id: "ws_preview",
    key: "design",
    name: "Product workspace",
  };
  const response = new Response(null, { headers: { ETag: '"v1"' } });
  http.GET.mockResolvedValue({ data: resource, response });
  http.PUT.mockResolvedValue({
    data: { ...resource, image_url: "/image.png" },
    response,
  });
  const view = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter
        initialEntries={[
          kind === "workspace"
            ? "/workspace/design/settings?section=profile"
            : "/organization/settings?section=profile",
        ]}
      >
        <Location />
        <Profile target={{ kind, id: resource.id }} editable={editable} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, user: userEvent.setup() };
}
it("opens image selection from the avatar and preserves the upload version and media type", async () => {
  const { container, user } = setup();
  const [button] = await screen.findAllByRole("button", {
    name: "Upload image",
  });
  const input =
    container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const click = vi.spyOn(input, "click");
  button.focus();
  await user.keyboard("{Enter}");
  expect(click).toHaveBeenCalledOnce();
  const file = new File(["image"], "avatar.png", { type: "image/png" });
  await user.upload(input, file);
  await waitFor(() => expect(http.PUT).toHaveBeenCalledOnce());
  expect(http.PUT).toHaveBeenCalledWith("/api/v1/workspaces/{workspace}/icon", {
    headers: { "If-Match": '"v1"', "Content-Type": "image/png" },
    params: {
      header: { "If-Match": '"v1"', "Content-Type": "image/png" },
      path: { workspace: "ws_preview" },
    },
    body: file,
  });
  expect(input.value).toBe("");
  await screen.findByRole("button", { name: "Remove image" });
});
it("keeps the image static when profile editing is unavailable", async () => {
  const { container } = setup(false);
  await waitFor(() =>
    expect(
      (
        screen.getByRole("textbox", {
          name: "Workspace name",
        }) as HTMLInputElement
      ).value,
    ).toBe("Product workspace"),
  );
  expect(screen.queryByRole("button", { name: "Upload image" })).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
});

function Location() {
  return <output aria-label="Current path">{useLocation().pathname}</output>;
}

it("changes a workspace key independently and navigates to its new address", async () => {
  const { user } = setup();
  http.PATCH.mockResolvedValue({
    data: { id: "ws_preview", key: "research", name: "Product workspace" },
    response: new Response(null, { headers: { ETag: '\"v2\"' } }),
  });
  const key = await screen.findByRole("textbox", { name: "URL key" });
  await user.clear(key);
  await user.type(key, "research");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "/workspace/research/settings",
    ),
  );
  expect(http.PATCH).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}",
    expect.objectContaining({
      body: { name: "Product workspace", key: "research" },
      params: {
        header: { "If-Match": '\"v1\"' },
        path: { workspace: "ws_preview" },
      },
    }),
  );
});

it("keeps the organization settings address when its key changes", async () => {
  const { user } = setup(true, "organization");
  http.PATCH.mockResolvedValue({
    data: { id: "ws_preview", key: "renamed", name: "Product workspace" },
    response: new Response(null, { headers: { ETag: '\"v2\"' } }),
  });
  const key = await screen.findByRole("textbox", { name: "URL key" });
  await user.clear(key);
  await user.type(key, "renamed");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull(),
  );
  expect(screen.getByLabelText("Current path").textContent).toBe(
    "/organization/settings",
  );
});
