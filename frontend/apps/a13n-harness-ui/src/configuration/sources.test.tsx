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
import { MemoryRouter, Route, Routes } from "react-router";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { DraftContext, SourceDocument, type SourceDraft } from "./sources";

vi.mock("./editor", () => ({ SourceEditor: () => null }));
vi.mock("./fields", () => ({ ResourceFields: () => null }));
afterEach(cleanup);

it("retains unsaved configuration on cancel and navigates only after confirmed discard", async () => {
  const path = "models/new.yaml";
  const draft: SourceDraft = {
    content: "name: My unsaved model\n",
    base: null,
    digest: null,
    replacement: false,
  };
  const drafts = new Map([[path, draft]]);
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client: {} } as Transport}>
        <DraftContext value={drafts}>
          <MemoryRouter initialEntries={["/settings/source"]}>
            <Routes>
              <Route
                path="/settings/source"
                element={<SourceDocument path={path} isNew />}
              />
              <Route path="/settings/resources" element={<h1>Resources</h1>} />
            </Routes>
          </MemoryRouter>
        </DraftContext>
      </TransportContext>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  let dialog = await screen.findByRole("dialog", {
    name: "Discard this unsaved configuration?",
  });
  expect(dialog.textContent).toContain(path);
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(drafts.get(path)).toBe(draft);
  expect(screen.queryByRole("heading", { name: "Resources" })).toBeNull();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  dialog = await screen.findByRole("dialog", {
    name: "Discard this unsaved configuration?",
  });
  await user.click(
    within(dialog).getByRole("button", { name: "Discard draft" }),
  );
  await screen.findByRole("heading", { name: "Resources" });
  expect(drafts.has(path)).toBe(false);
});

function mountEditor(kind: string, isNew = true) {
  const path = `${kind}s/${kind}-one.yaml`;
  const content = `kind: ${kind}\nid: ${kind}-one\nname: Example\ncustom: keep\n`;
  const drafts = new Map<string, SourceDraft>([
    [
      path,
      {
        content,
        base: isNew ? null : content,
        digest: isNew ? null : "original",
        replacement: isNew,
      },
    ],
  ]);
  const client = {
    GET: vi.fn(async () => ({
      data: {
        content,
        writable: true,
        content_available: true,
        source_digest: "original",
        resource_kind: kind,
      },
    })),
    POST: vi.fn(async () => ({ data: {} })),
    PUT: vi.fn(async () => ({ data: { source_digest: "saved" } })),
  };
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TransportContext value={{ client } as unknown as Transport}>
        <DraftContext value={drafts}>
          <MemoryRouter>
            <SourceDocument path={path} isNew={isNew} />
          </MemoryRouter>
        </DraftContext>
      </TransportContext>
    </QueryClientProvider>,
  );
  return { client, drafts, path, content, user: userEvent.setup() };
}

it.each(["model", "agent"])(
  "creates a %s directly without requiring a separate validation step",
  async (kind) => {
    const { client, drafts, path, content, user } = mountEditor(kind);
    expect(screen.getByRole("heading", { name: `Add ${kind}` })).toBeTruthy();
    expect(
      screen.getByText("Advanced configuration").closest("details")?.open,
    ).toBe(false);
    await user.click(screen.getByRole("button", { name: `Create ${kind}` }));
    await waitFor(() => expect(drafts.get(path)?.base).toBe(content));
    expect(client.PUT).toHaveBeenCalledWith(
      "/api/configuration/sources/{relative_path}",
      {
        params: { path: { relative_path: path } },
        body: { content },
      },
    );
    expect(client.POST).not.toHaveBeenCalled();
    expect(drafts.get(path)?.digest).toBe("saved");
    expect(screen.getByRole("status").textContent).toBe("Saved");
  },
);

it("checks without saving and keeps a rejected draft available for repair", async () => {
  const { client, drafts, path, content, user } = mountEditor("model");
  await user.click(screen.getByText("Advanced configuration"));
  await user.click(screen.getByRole("button", { name: "Check configuration" }));
  await screen.findByText("Configuration valid · not saved");
  expect(client.POST).toHaveBeenCalledWith("/api/configuration/validate", {
    params: { query: { path } },
    body: { content },
  });
  expect(client.PUT).not.toHaveBeenCalled();
  expect(drafts.get(path)?.base).toBeNull();
  client.PUT.mockRejectedValueOnce(new Error("Invalid configuration"));
  await user.click(screen.getByRole("button", { name: "Create model" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Invalid configuration",
  );
  expect(drafts.get(path)?.content).toBe(content);
  expect(drafts.get(path)?.base).toBeNull();
});

it("confirms cancelling an existing edit and restores the saved content without publishing", async () => {
  const { client, drafts, path, content, user } = mountEditor("agent", false);
  await screen.findByRole("button", { name: "Save changes" });
  await user.click(screen.getByText("Advanced configuration"));
  const id = screen.getByRole("textbox", { name: "Resource ID" });
  await user.clear(id);
  await user.type(id, "agent-edited");
  expect(drafts.get(path)?.content).toContain("custom: keep");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  let dialog = await screen.findByRole("dialog", {
    name: "Discard this local draft?",
  });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(drafts.get(path)?.content).toContain("agent-edited");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  dialog = await screen.findByRole("dialog", {
    name: "Discard this local draft?",
  });
  await user.click(
    within(dialog).getByRole("button", { name: "Reload saved version" }),
  );
  expect(drafts.get(path)?.content).toBe(content);
  expect(client.PUT).not.toHaveBeenCalled();
  expect(client.POST).not.toHaveBeenCalled();
});
