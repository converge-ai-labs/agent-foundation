import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Profile } from "./profile";

const http = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn() }));
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
function setup(editable = true) {
  const resource = { id: "ws_preview", name: "Product workspace" };
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
      <Profile
        target={{ kind: "workspace", id: resource.id }}
        editable={editable}
      />
    </QueryClientProvider>,
  );
  return { ...view, user: userEvent.setup() };
}
it("opens image selection from the avatar and preserves the upload version and media type", async () => {
  const { container, user } = setup();
  const button = await screen.findByRole("button", { name: "Upload image" });
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
