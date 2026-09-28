import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Profile } from "./profile";

const http = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
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
    id: kind === "workspace" ? "ws_preview" : "org_preview",
    organization_id: kind === "workspace" ? "org_preview" : undefined,
    name: "Product workspace",
  };
  const response = new Response(null, { headers: { ETag: '"v1"' } });
  http.GET.mockResolvedValue({ data: resource, response });
  http.PUT.mockResolvedValue({
    data: { ...resource, image_url: "/image.png" },
    response,
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <Profile target={{ kind, id: resource.id }} editable={editable} />
    </QueryClientProvider>,
  );
  return { ...view, cache, user: userEvent.setup() };
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
  expect(http.PUT).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace_id}/icon",
    {
      headers: { "If-Match": '"v1"', "Content-Type": "image/png" },
      params: {
        header: { "If-Match": '"v1"', "Content-Type": "image/png" },
        path: { workspace_id: "ws_preview" },
      },
      body: file,
    },
  );
  expect(input.value).toBe("");
  await screen.findByRole("button", { name: "Remove image" });
});
it("keeps the image static when profile editing is unavailable", async () => {
  const { container } = setup(false);
  expect(
    (await screen.findAllByText("Product workspace")).length,
  ).toBeGreaterThan(0);
  expect(screen.queryByRole("textbox", { name: "Workspace name" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Upload image" })).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
});

it("renames a workspace by name alone and refreshes the workspace list", async () => {
  const { cache, user } = setup();
  const renamed = {
    id: "ws_preview",
    organization_id: "org_preview",
    name: "Research",
  };
  cache.setQueryData(["workspaces", "org_preview"], {
    items: [{ id: "ws_preview", name: "Product workspace" }],
  });
  http.PATCH.mockResolvedValue({
    data: renamed,
    response: new Response(null, { headers: { ETag: '"v2"' } }),
  });
  const name = await screen.findByRole("textbox", { name: "Workspace name" });
  await user.clear(name);
  await user.type(name, "Research");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull(),
  );
  expect(http.PATCH).toHaveBeenCalledWith("/api/v1/workspaces/{workspace_id}", {
    body: { name: "Research" },
    headers: { "If-Match": '"v1"' },
    params: {
      header: { "If-Match": '"v1"' },
      path: { workspace_id: "ws_preview" },
    },
  });
  expect(cache.getQueryData(["workspaces", "org_preview"])).toEqual({
    items: [renamed],
  });
});

it("renames an organization in the signed-in identity", async () => {
  const { cache, user } = setup(true, "organization");
  const identity = {
    user: { value: { id: "usr_preview", name: "Ada" } },
    organizations: [{ id: "org_preview", name: "Product workspace" }],
  };
  cache.setQueryData(["identity"], identity);
  http.PATCH.mockResolvedValue({
    data: { id: "org_preview", name: "Acme" },
    response: new Response(null, { headers: { ETag: '"v2"' } }),
  });
  const name = await screen.findByRole("textbox", {
    name: "Organization name",
  });
  await user.clear(name);
  await user.type(name, "Acme");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull(),
  );
  expect(http.PATCH).toHaveBeenCalledWith(
    "/api/v1/organizations/{organization_id}",
    {
      body: { name: "Acme" },
      headers: { "If-Match": '"v1"' },
      params: {
        header: { "If-Match": '"v1"' },
        path: { organization_id: "org_preview" },
      },
    },
  );
  expect(cache.getQueryData(["identity"])).toEqual({
    ...identity,
    organizations: [{ id: "org_preview", name: "Acme" }],
  });
});
