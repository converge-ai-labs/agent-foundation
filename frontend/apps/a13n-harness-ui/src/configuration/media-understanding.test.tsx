// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { parse } from "yaml";
import { ModelsPage } from "./models";
import { DraftContext, type SourceDraft } from "./sources";

vi.mock("./editor", () => ({ SourceEditor: () => null }));
const state = vi.hoisted(() => ({
  content: "",
  selection: {} as Record<string, string>,
  put: vi.fn(),
  digest: "one",
}));
vi.mock("../transport/context", () => ({
  useSources: () => ({
    data: {
      sources: [
        {
          resource_kind: "root",
          relative_path: "custom.yaml",
          writable: true,
          resource_ids: [],
        },
      ],
    },
  }),
  useSelectors: () => ({
    data: {
      media_understanding: state.selection,
      media_understanding_environment: ["video"],
      models: [
        {
          model_id: "model-vision",
          name: "Vision",
          route: "test:vision",
          media_capabilities: ["image"],
        },
        {
          model_id: "model-text",
          name: "Text",
          route: "test:text",
          media_capabilities: [],
        },
      ],
    },
  }),
  useTransport: () => ({
    client: {
      GET: async () => ({
        data: {
          content: state.content,
          writable: true,
          content_available: true,
          source_digest: state.digest,
          resource_kind: "root",
        },
      }),
      PUT: async (...args: unknown[]) => state.put(...args),
    },
  }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function setup(dirty = false) {
  state.content = 'schema_version: "1"\nmax_goal_iterations: 17\n';
  state.selection = {};
  state.digest = "one";
  state.put.mockImplementation(async (_url, { body }) => {
    state.content = body.content;
    state.selection = parse(state.content).media_understanding ?? {};
    state.digest = "two";
    return { data: { source_digest: state.digest } };
  });
  const drafts = new Map<string, SourceDraft>();
  if (dirty)
    drafts.set("custom.yaml", {
      content: state.content + "display: {theme: dark}\n",
      base: state.content,
      digest: "one",
      replacement: false,
    });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DraftContext value={drafts}>
        <MemoryRouter>
          <ModelsPage />
        </MemoryRouter>
      </DraftContext>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), drafts };
}

it("selects compatible defaults, preserves root fields, saves and clears", async () => {
  const { user } = setup();
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  expect(
    screen.getByRole("combobox", { name: "Video understanding" }).textContent,
  ).toContain("Environment");
  expect(
    screen.getByRole("combobox", { name: "Audio understanding" }).textContent,
  ).toContain("Not configured");
  await user.click(image);
  expect(screen.queryByRole("option", { name: "Text" })).toBeNull();
  await user.click(await screen.findByRole("option", { name: "Vision" }));
  expect(state.put).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.put).toHaveBeenCalledTimes(1));
  expect(parse(state.content)).toEqual({
    schema_version: "1",
    max_goal_iterations: 17,
    media_understanding: { image: "model-vision" },
  });
  await user.click(image);
  await user.click(
    await screen.findByRole("option", { name: "Not configured" }),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
  expect(parse(state.content).media_understanding.image).toBeNull();
});

it("quick actions refresh the clean editor and disable unsupported purposes", async () => {
  const { user, drafts } = setup();
  await screen.findByRole("combobox", { name: "Image understanding" });
  await user.click(screen.getAllByRole("button", { name: "Set as…" })[0]);
  const video = await screen.findByRole("menuitem", {
    name: "Default video understanding",
  });
  expect(video.getAttribute("aria-disabled")).toBe("true");
  await user.click(
    screen.getByRole("menuitem", { name: "Default image understanding" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Image understanding" }).textContent,
    ).toContain("Vision"),
  );
  expect(drafts.get("custom.yaml")?.content).toBe(
    drafts.get("custom.yaml")?.base,
  );
  expect(
    screen.getByRole("button", { name: "Save" }).hasAttribute("disabled"),
  ).toBe(true);
});

it("does not quick-save an existing root draft and supports confirmed discard", async () => {
  const { user, drafts } = setup(true);
  await screen.findByRole("combobox", { name: "Image understanding" });
  await user.click(screen.getAllByRole("button", { name: "Set as…" })[0]);
  await user.click(
    await screen.findByRole("menuitem", {
      name: "Default image understanding",
    }),
  );
  await screen.findByText(
    "Save or discard the root configuration draft first.",
  );
  expect(state.put).not.toHaveBeenCalled();
  expect(drafts.get("custom.yaml")?.content).toContain("theme: dark");
  await user.click(screen.getByRole("button", { name: "Discard" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Reload saved version" }),
  );
  await waitFor(() =>
    expect(drafts.get("custom.yaml")?.content).toBe(state.content),
  );
});
