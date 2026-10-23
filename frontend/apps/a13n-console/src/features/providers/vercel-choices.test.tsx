import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { VercelChoices } from "./vercel-choices";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ POST: post }) }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("loads choices with the current draft credential and clears stale replies on credential change", async () => {
  let finish: (value: unknown) => void = () => {};
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const update = vi.fn();
  const view = render(
    <VercelChoices
      credential={{ api_key: "first-key" }}
      value={{}}
      onChange={update}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Load Vercel teams" }));
  const signal = post.mock.calls.at(-1)![1].signal as AbortSignal;
  expect(post.mock.calls.at(-1)![1].body.api_key).toBe("first-key");
  view.rerender(
    <VercelChoices
      credential={{ api_key: "second-key" }}
      value={{}}
      onChange={update}
    />,
  );
  expect(signal.aborted).toBe(true);
  finish({
    data: { items: [{ id: "team_old", name: "Old account" }], cursor: null },
  });
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Load Vercel teams" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent
    .setup()
    .click(screen.getByRole("combobox", { name: "Vercel team" }));
  expect(screen.queryByRole("option", { name: "Old account" })).toBeNull();
});
