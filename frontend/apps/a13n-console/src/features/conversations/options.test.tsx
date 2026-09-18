import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RunOptions, useRunOptions } from "./options";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("distinguishes new allocation from reuse and submits the selected identity", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.includes("environment-templates")
        ? [{ id: "envt_test", name: "Research" }]
        : path.endsWith("/environments")
          ? [
              { id: "env_first", name: "Research" },
              { id: "env_second", name: "Research" },
            ]
          : [],
    },
  }));
  const submit = vi.fn();
  function ComposerOptions() {
    const options = useRunOptions();
    return (
      <>
        <RunOptions options={options} />
        <button onClick={() => submit(options.build())}>Submit</button>
      </>
    );
  }
  const cache = new QueryClient();
  render(
    <QueryClientProvider client={cache}>
      <ComposerOptions />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Create from template: Research",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Done" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ environment: { template_id: "envt_test" } }),
  );
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  expect(
    screen.getByRole("option", {
      name: "Reuse existing: Research (env_first)",
    }),
  ).toBeTruthy();
  await user.click(
    screen.getByRole("option", {
      name: "Reuse existing: Research (env_second)",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Done" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ environment: { environment_id: "env_second" } }),
  );
  cache.clear();
});
