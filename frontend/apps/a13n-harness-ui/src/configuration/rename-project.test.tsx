// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { RenameProject } from "./rename-project";
import { DraftContext, type SourceDraft } from "./sources";

const path = "projects/custom-name.yaml";
let content: string;
let writes: Request[];
let writable: boolean;
let failSave: boolean;
let queries: QueryClient;
let drafts: Map<string, SourceDraft>;
const close = vi.fn();
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  content =
    '# Keep this comment\nschema_version: "1"\nkind: project\nid: project-one\nname: One\nposition: 9\nroots: [{path: /one}, {path: /two}]\ndefaults: {agent: agent-custom}\n';
  writes = [];
  writable = true;
  failSave = false;
  drafts = new Map();
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (request.method === "PUT") {
        writes.push(request.clone());
        if (failSave)
          return json({ error: { message: "Configuration is invalid." } }, 422);
        return json({ source_digest: "saved" });
      }
      if (url.pathname === "/api/configuration/sources")
        return json({
          sources: [
            {
              relative_path: path,
              resource_kind: "project",
              resource_ids: ["project-one"],
            },
          ],
        });
      if (
        decodeURIComponent(url.pathname) ===
        `/api/configuration/sources/${path}`
      )
        return json({
          content,
          writable,
          content_available: true,
          source_digest: "v1",
        });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
function mount() {
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter>
          <DraftContext value={drafts}>
            <RenameProject projectId="project-one" name="One" close={close} />
          </DraftContext>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
it("renames through the actual source path using fresh fields and preserves IDs, roots, defaults, and comments", async () => {
  drafts.set(path, {
    content,
    base: content,
    digest: "v1",
    replacement: false,
  });
  mount();
  fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), {
    target: { value: " New: name " },
  });
  const save = screen.getByRole("button", { name: "Save name" });
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  content = content.replace("position: 9", "position: 12");
  const latest = parse(content);
  fireEvent.click(save);
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(writes).toHaveLength(1);
  expect(decodeURIComponent(new URL(writes[0].url).pathname)).toBe(
    `/api/configuration/sources/${path}`,
  );
  const submitted = (await writes[0].json()).content;
  expect(parse(submitted)).toEqual({ ...latest, name: "New: name" });
  expect(submitted).toContain("# Keep this comment");
  expect(drafts.get(path)).toEqual({
    content: submitted,
    base: submitted,
    digest: "saved",
    replacement: false,
  });
});
it("does not overwrite an unsaved Project settings draft", async () => {
  const draft = {
    content: content + "# unsaved\n",
    base: content,
    digest: "v1",
    replacement: false,
  };
  drafts.set(path, draft);
  mount();
  await screen.findByText(/There are unsaved changes/);
  fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), {
    target: { value: "Changed" },
  });
  expect(
    (screen.getByRole("button", { name: "Save name" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    screen.getByRole("link", { name: "Project settings" }).getAttribute("href"),
  ).toBe("/projects/project-one");
  expect(drafts.get(path)).toBe(draft);
  expect(writes).toHaveLength(0);
});
it("keeps failed names editable and does not claim publication succeeded", async () => {
  failSave = true;
  mount();
  fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), {
    target: { value: "Retained" },
  });
  const save = screen.getByRole("button", { name: "Save name" });
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(save);
  await screen.findByText("Configuration is invalid.");
  expect(
    (screen.getByRole("textbox", { name: "Project name" }) as HTMLInputElement)
      .value,
  ).toBe("Retained");
  expect(close).not.toHaveBeenCalled();
  expect(writes).toHaveLength(1);
});
it("does not submit a read-only source", async () => {
  writable = false;
  mount();
  await screen.findByText("This project source is unavailable or read only.");
  fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), {
    target: { value: "Changed" },
  });
  expect(
    (screen.getByRole("button", { name: "Save name" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(writes).toHaveLength(0);
});
