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
import { ImportSkill } from "./import-dialog";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
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

it("switches source with the keyboard and retains the GitHub draft", async () => {
  const user = setup();
  await user.click(screen.getByRole("button", { name: "Import skill" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.queryByRole("combobox")).toBeNull();
  await user.click(dialog.getByRole("button", { name: "ZIP file" }));
  await user.keyboard("{ArrowRight}{Enter}");
  const repository = await dialog.findByRole("textbox", {
    name: "Repository URL",
  });
  await user.click(repository);
  await user.paste("https://github.com/example/skill");
  await user.click(dialog.getByRole("button", { name: "ZIP file" }));
  await waitFor(() =>
    expect(
      dialog.queryByRole("textbox", { name: "Repository URL" }),
    ).toBeNull(),
  );
  expect(
    (dialog.getByRole("button", { name: "Import" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(dialog.getByRole("button", { name: "GitHub" }));
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

it("publishes a new version using the chosen GitHub source and the skill the user saw", async () => {
  const skill = {
    id: "sk_test",
    name: "Test skill",
    version: 3,
  } as Schema["Skill"];
  http.GET.mockResolvedValue({
    data: { items: [{ number: 2 }], next_cursor: null },
    response: new Response(),
  });
  http.POST.mockResolvedValue({ data: {}, response: new Response() });
  const user = setup(skill);
  await user.click(screen.getByRole("button", { name: "New version" }));
  const dialog = within(
    await screen.findByRole("dialog", { name: /New version of/ }),
  );
  expect(dialog.queryByRole("textbox", { name: "Display name" })).toBeNull();
  await user.click(dialog.getByRole("button", { name: "GitHub" }));
  await user.click(dialog.getByRole("textbox", { name: "Repository URL" }));
  await user.paste("https://github.com/example/skill");
  await user.click(dialog.getByRole("button", { name: "Advanced settings" }));
  await user.type(
    dialog.getByRole("textbox", { name: "Subdirectory" }),
    "skills/review",
  );
  await user.click(dialog.getByRole("button", { name: "Publish version" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(1));
  expect(http.POST.mock.calls[0][0]).toBe(
    "/api/v1/skills/{skill_id}/revisions",
  );
  expect(http.POST.mock.calls[0][1]).toEqual({
    params: { path: { skill_id: "sk_test" } },
    headers: { "If-Match": '"sk_test:3"' },
    body: {
      source: {
        kind: "github",
        repository: "example/skill",
        path: "skills/review",
      },
    },
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("requires validation of the currently selected ZIP before publishing", async () => {
  const manifest = {
    name: "Review documents",
    description: "Review a document for clarity.",
    root: "",
    files: [{ path: "SKILL.md", size: 100 }],
    size: 100,
    package_digest: "0".repeat(64),
    package_size: 90,
    source: { kind: "upload", upload_id: "upl_test" },
  };
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("/uploads")
      ? {
          upload_id: "upl_test",
          filename: "review.zip",
          content_type: "application/zip",
          size: 90,
          digest: "0".repeat(64),
        }
      : path.endsWith("/skills/validate")
        ? manifest
        : { id: "sk_new" },
    response: new Response(),
  }));
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ImportSkill />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Import skill" }));
  const publish = screen
    .getByRole("dialog")
    .querySelector<HTMLButtonElement>('button[type="submit"]')!;
  expect(publish.disabled).toBe(true);
  const input = screen
    .getByRole("dialog")
    .querySelector<HTMLInputElement>('input[type="file"]')!;
  await user.upload(
    input,
    new File(["test"], "review.zip", { type: "application/zip" }),
  );
  await user.click(screen.getByRole("button", { name: "Validate package" }));
  const receipt = within(await screen.findByRole("status"));
  expect(receipt.getByText("Review documents")).toBeTruthy();
  expect(receipt.getByText("v1")).toBeTruthy();
  expect(receipt.getByText("{{count}} files")).toBeTruthy();
  expect(receipt.getByText("Review a document for clarity.")).toBeTruthy();
  await waitFor(() => expect(publish.disabled).toBe(false));
  const [uploadPath, upload] = http.POST.mock.calls[0];
  expect(uploadPath).toBe("/api/v1/uploads");
  expect(upload.params.path).toBeUndefined();
  expect(upload.params.header["Idempotency-Key"]).toBeTruthy();
  expect(upload.body.file).toHaveProperty("name", "review.zip");
  expect(upload.bodySerializer().get("file")).toBe(upload.body.file);
  expect(http.POST.mock.calls[1]).toEqual([
    "/api/v1/skills/validate",
    {
      body: { source: { kind: "upload", upload_id: "upl_test" } },
    },
  ]);
  await user.upload(
    input,
    new File(["changed"], "updated.zip", { type: "application/zip" }),
  );
  expect(publish.disabled).toBe(true);
  expect(screen.queryByRole("status")).toBeNull();
  expect(http.POST).toHaveBeenCalledTimes(2);
});

it("imports the validated package it previewed", async () => {
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("/uploads")
      ? { upload_id: "upl_test", filename: "review.zip", size: 4 }
      : path.endsWith("/skills/validate")
        ? {
            name: "Review documents",
            description: "",
            files: [],
            size: 4,
            source: { kind: "upload", upload_id: "upl_test" },
          }
        : { id: "sk_new" },
    response: new Response(),
  }));
  const user = setup();
  await user.click(screen.getByRole("button", { name: "Import skill" }));
  const dialog = within(await screen.findByRole("dialog"));
  await user.upload(
    screen
      .getByRole("dialog")
      .querySelector<HTMLInputElement>('input[type="file"]')!,
    new File(["test"], "review.zip", { type: "application/zip" }),
  );
  await user.click(dialog.getByRole("button", { name: "Validate package" }));
  await dialog.findByRole("status");
  await user.click(dialog.getByRole("button", { name: "Import" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(3));
  expect(http.POST.mock.calls[2]).toEqual([
    "/api/v1/skills",
    {
      body: { source: { kind: "upload", upload_id: "upl_test" } },
    },
  ]);
});
