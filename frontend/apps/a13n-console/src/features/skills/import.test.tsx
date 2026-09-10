import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ImportSkill } from "./import";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function setup(skill?: Schema["Skill"]) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <ImportSkill skill={skill} />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("uses source tabs with keyboard selection and retains the GitHub draft", async () => {
  const user = setup();
  await user.click(screen.getByRole("button", { name: "Import skill" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.queryByRole("combobox")).toBeNull();
  await user.click(dialog.getByRole("tab", { name: "ZIP file" }));
  await user.keyboard("{ArrowRight}{Enter}");
  const repository = await dialog.findByRole("textbox", {
    name: "Repository URL",
  });
  await user.type(repository, "https://github.com/example/skill");
  await user.click(dialog.getByRole("tab", { name: "ZIP file" }));
  await waitFor(() =>
    expect(
      dialog.queryByRole("textbox", { name: "Repository URL" }),
    ).toBeNull(),
  );
  await user.click(dialog.getByRole("button", { name: "Import skill" }));
  await dialog.findByText("Validate your ZIP file before publishing.");
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(dialog.getByRole("tab", { name: "GitHub" }));
  expect(
    (
      dialog.getByRole("textbox", {
        name: "Repository URL",
      }) as HTMLInputElement
    ).value,
  ).toBe("https://github.com/example/skill");
  expect(
    dialog.queryByText("Validate your ZIP file before publishing."),
  ).toBeNull();
});

it("publishes a new version using the chosen GitHub source and current version", async () => {
  const skill = {
    id: "sk_test",
    name: "Test skill",
    version: 3,
  } as Schema["Skill"];
  http.POST.mockResolvedValue({ data: { skill }, response: new Response() });
  const user = setup(skill);
  await user.click(screen.getByRole("button", { name: "New version" }));
  const dialog = within(
    await screen.findByRole("dialog", { name: "New version" }),
  );
  expect(dialog.queryByRole("textbox", { name: "Display name" })).toBeNull();
  await user.click(dialog.getByRole("tab", { name: "GitHub" }));
  await user.type(
    dialog.getByRole("textbox", { name: "Repository URL" }),
    "https://github.com/example/skill",
  );
  await user.type(
    dialog.getByRole("textbox", { name: "Subdirectory" }),
    "skills/review",
  );
  await user.click(dialog.getByRole("button", { name: "Publish version" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(1));
  expect(http.POST.mock.calls[0][0]).toBe(
    "/api/v1/skills/{skill_id}/revisions",
  );
  expect(http.POST.mock.calls[0][1].body).toEqual({
    expected_version: 3,
    source: {
      kind: "github",
      repository_url: "https://github.com/example/skill",
      subdirectory: "skills/review",
    },
  });
  expect(http.POST.mock.calls[0][1].params.header["X-A13N-Workspace-ID"]).toBe(
    "ws_test",
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
