import { ApiError } from "../../service-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { EnvironmentNameEditor } from "./instance-name";

const http = vi.hoisted(() => ({ PATCH: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("retains the draft and original precondition until an explicit conflict reload", async () => {
  const user = userEvent.setup();
  const reload = vi.fn().mockResolvedValue(undefined);
  const cache = new QueryClient();
  const invalidate = vi.spyOn(cache, "invalidateQueries");
  http.PATCH.mockRejectedValueOnce(
    new ApiError(412, "precondition_failed", "Changed", {}, null),
  ).mockResolvedValueOnce({ data: { id: "env_test", name: "My draft" } });
  function editor(name: string, etag: string, key = 0) {
    return (
      <QueryClientProvider client={cache}>
        <EnvironmentNameEditor
          key={key}
          environment={{ id: "env_test", name, workspace_id: "ws_test" }}
          etag={etag}
          reload={reload}
        />
      </QueryClientProvider>
    );
  }
  const view = render(editor("Original", '"old"'));
  await user.clear(screen.getByRole("textbox", { name: "Name" }));
  await user.type(screen.getByRole("textbox", { name: "Name" }), "My draft");
  view.rerender(editor("Changed elsewhere", '"fresh"'));
  await user.click(screen.getByRole("button", { name: "Save name" }));
  await screen.findByText("This resource changed");
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("My draft");
  expect(http.PATCH.mock.calls[0][1].headers).toEqual({
    "If-Match": '"old"',
  });
  expect(reload).not.toHaveBeenCalled();
  expect(invalidate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Reload" }));
  expect(http.PATCH).toHaveBeenCalledOnce();
  view.rerender(editor("Changed elsewhere", '"fresh"', 1));
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Changed elsewhere");
  await user.clear(screen.getByRole("textbox", { name: "Name" }));
  await user.type(screen.getByRole("textbox", { name: "Name" }), "My draft");
  await user.click(screen.getByRole("button", { name: "Save name" }));
  await waitFor(() => expect(reload).toHaveBeenCalledTimes(2));
  expect(http.PATCH.mock.calls[1][1]).toEqual({
    params: { path: { environment_id: "env_test" } },
    headers: { "If-Match": '"fresh"' },
    body: { name: "My draft" },
  });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["environments"] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run-options"] });
});
