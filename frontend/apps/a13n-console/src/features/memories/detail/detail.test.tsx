// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router";
import { createClient } from "../../../service-client";
import type { Schema } from "../../../shared/api";
import { MemoryDetail } from "../page";

vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    organization: { id: "org_1" },
    workspace: { id: "ws_1" },
    can: (verb: string) => permissions.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values?.[name])),
    i18n: { resolvedLanguage: "en" },
  }),
}));

let permissions: string[];
let client: ReturnType<typeof createClient>;
let cache: QueryClient;

afterEach(() => {
  client?.close();
  cache?.clear();
});

const AT = "2026-09-20T10:00:00Z";

function memoryFile(
  id: string,
  path: string,
  content: string,
  version: number,
): Schema["MemoryFile"] {
  return {
    id,
    path,
    content,
    version,
    size: content.length,
    description: null,
    created_at: AT,
    updated_at: AT,
    updated_by_principal_id: "usr_1",
    updated_by_run_id: null,
  };
}

function revision(
  seq: number,
  op: Schema["MemoryRevision"]["op"],
  path: string,
  run_id: string | null = null,
): Schema["MemoryRevision"] {
  return {
    seq,
    op,
    path,
    run_id,
    moved_path: null,
    principal_id: run_id ? null : "usr_1",
    tool_call_id: run_id ? "call_1" : null,
    created_at: AT,
  };
}

/** One memory behind the Service's routes, with conditional writes. */
function setup(search = "", allowed = ["read", "run", "write"]) {
  permissions = allowed;
  const memory: Schema["Memory"] = {
    id: "mem_1",
    organization_id: "org_1",
    workspace_id: "ws_1",
    key: "handbook",
    name: "Team handbook",
    description: "How the team works",
    kind: "file",
    type: "postgres",
    provider_id: null,
    namespace: null,
    guide: null,
    inherited_guide: "Keep one topic per file.",
    always_load: ["README.md"],
    labels: { team: "docs" },
    file_count: 3,
    content_bytes: 64,
    history_bytes: 32,
    version: 3,
    created_by_id: "usr_1",
    updated_by_id: "usr_1",
    created_at: AT,
    updated_at: AT,
  };
  const files = new Map(
    [
      memoryFile("memf_readme", "README.md", "# Handbook\n\nStart here.", 3),
      memoryFile("memf_glossary", "glossary.md", "# Glossary", 2),
      memoryFile("memf_releases", "process/releases.md", "Ship Fridays.", 5),
    ].map((file) => [file.path, file]),
  );
  const revisions = [
    revision(6, "update", "process/releases.md"),
    revision(5, "update", "README.md", "run_edit"),
    revision(4, "delete", "old.md"),
    revision(1, "create", "README.md"),
  ];
  const requests: {
    method: string;
    path: string;
    query: URLSearchParams;
    ifMatch: string | null;
    body: unknown;
  }[] = [];
  const tag = (row: { id: string; version: number }) =>
    `"${row.id}:${row.version}"`;
  const failed = (status: number, code: string) =>
    Response.json({ error: { code, message: code } }, { status });
  const base = "/api/v1/workspaces/ws_1/memories/mem_1";
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const path = decodeURIComponent(url.pathname);
    const body = ["POST", "PUT", "PATCH"].includes(request.method)
      ? await request.json().catch(() => undefined)
      : undefined;
    const ifMatch = request.headers.get("If-Match");
    requests.push({
      method: request.method,
      path,
      query: url.searchParams,
      ifMatch,
      body,
    });
    const route = `${request.method} ${path}`;
    if (route === `GET ${base}`)
      return Response.json(memory, { headers: { ETag: tag(memory) } });
    if (route === `PATCH ${base}`) {
      if (ifMatch !== tag(memory)) return failed(412, "precondition_failed");
      Object.assign(memory, body, { version: memory.version + 1 });
      return Response.json(memory, { headers: { ETag: tag(memory) } });
    }
    if (route === `DELETE ${base}`) return new Response(null, { status: 204 });
    if (route === `GET ${base}/files`)
      return Response.json({
        items: [...files.values()]
          .sort((a, b) => a.path.localeCompare(b.path))
          .map(({ content: _, ...entry }) => entry),
        next_cursor: null,
      });
    if (route === `POST ${base}/files`) {
      const { path: created, content } = body as Schema["MemoryFileCreate"];
      const file = memoryFile("memf_new", created, content, 7);
      files.set(created, file);
      return Response.json(file, { status: 201, headers: { ETag: tag(file) } });
    }
    if (route === `POST ${base}/files/move`) {
      const { source, destination } = body as Schema["MemoryFileMove"];
      const file = files.get(source)!;
      if (ifMatch !== tag(file)) return failed(412, "precondition_failed");
      files.delete(source);
      const moved = { ...file, path: destination, version: 8 };
      files.set(destination, moved);
      return Response.json(moved, { headers: { ETag: tag(moved) } });
    }
    if (path.startsWith(`${base}/files/`)) {
      const file = files.get(path.slice(`${base}/files/`.length));
      if (!file) return failed(404, "not_found");
      if (request.method === "GET")
        return Response.json(file, { headers: { ETag: tag(file) } });
      if (ifMatch !== tag(file)) return failed(412, "precondition_failed");
      if (request.method === "DELETE") {
        files.delete(file.path);
        return new Response(null, { status: 204 });
      }
      const { content } = body as Schema["MemoryFileReplace"];
      Object.assign(file, { content, version: file.version + 1 });
      return Response.json(file, { headers: { ETag: tag(file) } });
    }
    if (route === `GET ${base}/revisions`) {
      const filePath = url.searchParams.get("path"),
        run = url.searchParams.get("run_id");
      return Response.json({
        items: revisions.filter(
          (item) =>
            (!filePath || item.path === filePath) &&
            (!run || item.run_id === run),
        ),
        next_cursor: null,
      });
    }
    if (route === `DELETE ${base}/revisions`)
      return Response.json({ purged: 1 });
    const detail = /^\/revisions\/(\d+)(\/restore)?$/.exec(
      path.slice(base.length),
    );
    if (detail) {
      const item = revisions.find((entry) => entry.seq === Number(detail[1]))!;
      if (detail[2])
        return Response.json({
          path: item.path,
          file: files.get(item.path) ?? null,
        });
      return Response.json({
        ...item,
        previous_content: "old line",
        content: "new line",
        hunks: ["@@ -1 +1 @@\n-old line\n+new line"],
      });
    }
    if (route === "GET /api/v1/workspaces/ws_1/runs/run_edit")
      return Response.json({
        id: "run_edit",
        session_id: "ses_1",
        thread_id: "thr_1",
      });
    return failed(404, "not_found");
  };
  client = createClient({
    baseUrl: "http://localhost",
    auth: { type: "session", csrfToken: "test-csrf" },
    fetch: fetcher,
    maxReadRetries: 0,
  });
  cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[`/workspace/design/memories/mem_1${search}`]}
      >
        <Routes>
          <Route element={<Located />}>
            <Route
              path="/workspace/:workspaceKey/memories/:memoryId"
              element={<MemoryDetail />}
            />
            <Route
              path="/workspace/:workspaceKey/memories"
              element={<p>Memory collection</p>}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  /** Someone else changes a file or the memory after the page read it. */
  function changeElsewhere(filePath?: string) {
    if (filePath) {
      const file = files.get(filePath)!;
      Object.assign(file, {
        content: "Changed elsewhere.",
        version: file.version + 1,
      });
    } else
      Object.assign(memory, {
        name: "Renamed elsewhere",
        version: memory.version + 1,
      });
  }
  const sent = (method: string, suffix = "") =>
    requests.filter(
      (item) => item.method === method && item.path.startsWith(base + suffix),
    );
  return { user: userEvent.setup(), sent, changeElsewhere, memory };
}

function Located() {
  const location = useLocation();
  return (
    <>
      <output data-testid="location">
        {location.pathname + location.search}
      </output>
      <Outlet />
    </>
  );
}

const location = () => screen.getByTestId("location").textContent ?? "";

it("opens the first always-loaded file and saves an edit under the ETag it started from", async () => {
  const { user, sent, changeElsewhere } = setup();
  await screen.findByRole("heading", { name: "Team handbook" });
  expect(await screen.findByRole("heading", { name: "Handbook" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /^README\.md\s*Always loaded$/ }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Edit" }));
  const editor = screen.getByRole("textbox", { name: "Content of README.md" });
  await user.clear(editor);
  await user.type(editor, "My notes");
  changeElsewhere("README.md");
  await user.click(screen.getByRole("button", { name: "Save file" }));
  // The conflict keeps the text, and saving waits for a choice.
  await screen.findByText("This file changed");
  expect(
    (
      screen.getByRole("textbox", {
        name: "Content of README.md",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("My notes");
  expect(
    (screen.getByRole("button", { name: "Save file" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Keep my text" }));
  await waitFor(() =>
    expect(screen.queryByText("This file changed")).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Save file" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("textbox", { name: "Content of README.md" }),
    ).toBeNull(),
  );
  expect(sent("PUT").map(({ ifMatch, body }) => [ifMatch, body])).toEqual([
    ['"memf_readme:3"', { content: "My notes" }],
    ['"memf_readme:4"', { content: "My notes" }],
  ]);
});

it("discards a conflicting edit and shows the saved file", async () => {
  const { user, changeElsewhere } = setup("?file=glossary.md");
  await screen.findByRole("heading", { name: "Glossary" });
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(
    screen.getByRole("textbox", { name: "Content of glossary.md" }),
    " more",
  );
  changeElsewhere("glossary.md");
  await user.click(screen.getByRole("button", { name: "Save file" }));
  await user.click(
    await screen.findByRole("button", { name: "Discard my text and reload" }),
  );
  expect(await screen.findByText("Changed elsewhere.")).toBeTruthy();
  expect(
    screen.queryByRole("textbox", { name: "Content of glossary.md" }),
  ).toBeNull();
});

it("creates, renames and deletes files", async () => {
  const { user, sent } = setup();
  await screen.findByRole("heading", { name: "Handbook" });
  await user.click(screen.getByRole("button", { name: "New file" }));
  const create = await screen.findByRole("dialog");
  await user.type(
    within(create).getByRole("textbox", { name: "Path" }),
    "notes/topic.md",
  );
  await user.type(
    within(create).getByRole("textbox", { name: "Content" }),
    "Topic",
  );
  await user.click(within(create).getByRole("button", { name: "Create file" }));
  await waitFor(() => expect(location()).toContain("file=notes%2Ftopic.md"));
  expect(sent("POST", "/files")[0]?.body).toEqual({
    path: "notes/topic.md",
    content: "Topic",
  });
  await screen.findByRole("region", { name: "notes/topic.md" });

  await user.click(screen.getByRole("button", { name: "glossary.md" }));
  await screen.findByRole("heading", { name: "Glossary" });
  await user.click(screen.getByRole("button", { name: "Rename" }));
  const rename = await screen.findByRole("dialog");
  const destination = within(rename).getByRole("textbox", { name: "New path" });
  await user.clear(destination);
  await user.type(destination, "terms.md");
  await user.click(within(rename).getByRole("button", { name: "Rename file" }));
  await waitFor(() => expect(location()).toContain("file=terms.md"));
  expect(sent("POST", "/files/move")[0]).toMatchObject({
    ifMatch: '"memf_glossary:2"',
    body: { source: "glossary.md", destination: "terms.md" },
  });

  await screen.findByRole("region", { name: "terms.md" });
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  const confirm = await screen.findByRole("dialog");
  await user.click(
    within(confirm).getByRole("button", { name: "Delete file" }),
  );
  await waitFor(() => expect(sent("DELETE", "/files/")).toHaveLength(1));
  expect(sent("DELETE", "/files/")[0]).toMatchObject({
    path: "/api/v1/workspaces/ws_1/memories/mem_1/files/terms.md",
    ifMatch: '"memf_glossary:8"',
  });
});

it("filters history by run from the URL, opens a diff and restores under the current file", async () => {
  const { user, sent } = setup("?tab=history&run=run_edit");
  const run = await screen.findAllByRole("link", { name: "run_edit" });
  await waitFor(() =>
    expect(run[0]?.getAttribute("href")).toBe(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_edit?view=debug",
    ),
  );
  expect(sent("GET", "/revisions")[0]?.query.get("run_id")).toBe("run_edit");
  expect(screen.getByText("#5")).toBeTruthy();
  expect(screen.queryByText("#6")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Changes" }));
  expect(await screen.findByText("+new line")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Restore" }));
  const confirm = await screen.findByRole("dialog");
  await user.click(
    within(confirm).getByRole("button", { name: "Restore file" }),
  );
  await waitFor(() =>
    expect(sent("POST", "/revisions/5/restore")).toHaveLength(1),
  );
  expect(sent("POST", "/revisions/5/restore")[0]?.ifMatch).toBe(
    '"memf_readme:3"',
  );
  // Clearing the run shows every change again.
  await user.click(
    screen.getByRole("button", { name: "Show changes by every run" }),
  );
  await screen.findByText("#6");
  expect(location()).not.toContain("run=");
});

it("restores a deleted file without a precondition", async () => {
  const { user, sent } = setup("?tab=history&path=old.md");
  await screen.findByText("#4");
  await user.click(screen.getByRole("button", { name: "Restore" }));
  const confirm = await screen.findByRole("dialog");
  expect(
    within(confirm).getByText(
      "The path returns to its content before this change. The restore is recorded as a new change.",
    ),
  ).toBeTruthy();
  await user.click(
    within(confirm).getByRole("button", { name: "Restore file" }),
  );
  await waitFor(() =>
    expect(sent("POST", "/revisions/4/restore")).toHaveLength(1),
  );
  expect(sent("POST", "/revisions/4/restore")[0]?.ifMatch).toBeNull();
});

it("purges one file's history only for people who may configure the memory", async () => {
  const { user, sent } = setup("?tab=history&path=process/releases.md");
  await screen.findByText("#6");
  await user.click(screen.getByRole("button", { name: "Delete history" }));
  const confirm = await screen.findByRole("dialog");
  expect(
    within(confirm).getByText(
      /stays in their conversation histories and traces/,
    ),
  ).toBeTruthy();
  await user.click(
    within(confirm).getByRole("button", { name: "Delete file history" }),
  );
  await waitFor(() => expect(sent("DELETE", "/revisions")).toHaveLength(1));
  expect(sent("DELETE", "/revisions")[0]?.query.get("path")).toBe(
    "process/releases.md",
  );
});

it("offers no purge to people who may only run agents", async () => {
  setup("?tab=history&path=process/releases.md", ["read", "run"]);
  await screen.findByText("#6");
  expect(screen.getByRole("button", { name: "Restore" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Delete history" })).toBeNull();
});

it("switches the guide between inherited, custom and none", async () => {
  const { user, sent } = setup("?tab=configuration");
  expect((await screen.findByLabelText("Inherited guide")).textContent).toBe(
    "Keep one topic per file.",
  );
  // Customizing starts from the guide the memory inherited.
  await user.click(screen.getByRole("button", { name: "Custom" }));
  const guide = screen.getByRole("textbox", { name: "Guide" });
  expect((guide as HTMLTextAreaElement).value).toBe("Keep one topic per file.");
  await user.type(guide, " Link related files.");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(sent("PATCH")).toHaveLength(1));
  expect(sent("PATCH")[0]).toMatchObject({
    ifMatch: '"mem_1:3"',
    body: { guide: "Keep one topic per file. Link related files." },
  });
  await waitFor(() =>
    expect(
      screen.queryByRole("region", { name: "Unsaved changes" }),
    ).toBeNull(),
  );

  await user.click(screen.getByRole("button", { name: "None" }));
  expect(
    screen.getByText("Runs receive no guide for this memory."),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(sent("PATCH")).toHaveLength(2));
  expect(sent("PATCH")[1]).toMatchObject({
    ifMatch: '"mem_1:4"',
    body: { guide: "" },
  });

  await waitFor(() =>
    expect(
      screen.queryByRole("region", { name: "Unsaved changes" }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Inherit" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(sent("PATCH")).toHaveLength(3));
  expect(sent("PATCH")[2]?.body).toEqual({ guide: null });
});

it("edits always-loaded paths and keeps the draft through a conflict", async () => {
  const { user, sent, changeElsewhere } = setup("?tab=configuration");
  await screen.findByRole("heading", { name: "Always loaded files" });
  await user.type(
    screen.getByRole("combobox", { name: "Path to always load" }),
    "missing.md",
  );
  await user.click(screen.getByRole("button", { name: "Add path" }));
  expect(await screen.findByText("No file at this path yet")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Remove README.md" }));
  changeElsewhere();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("This memory changed");
  await user.click(
    screen.getByRole("button", {
      name: "Load current version and keep my draft",
    }),
  );
  await waitFor(() =>
    expect(screen.queryByText("This memory changed")).toBeNull(),
  );
  expect(screen.getByText("missing.md")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(sent("PATCH")).toHaveLength(2));
  expect(sent("PATCH").map(({ ifMatch, body }) => [ifMatch, body])).toEqual([
    ['"mem_1:3"', { always_load: ["missing.md"] }],
    // Only the draft's own change is sent over the newer memory.
    ['"mem_1:4"', { always_load: ["missing.md"] }],
  ]);
  // The other change is kept, not overwritten.
  expect(
    screen.getByRole("heading", { name: "Renamed elsewhere" }),
  ).toBeTruthy();
});

it("deletes the memory and returns to the collection", async () => {
  const { user, sent } = setup("?tab=configuration");
  await screen.findByRole("heading", { name: "Always loaded files" });
  await user.click(screen.getByRole("button", { name: "Delete memory" }));
  const confirm = await screen.findByRole("dialog");
  await user.click(
    within(confirm).getByRole("button", { name: "Delete memory" }),
  );
  await screen.findByText("Memory collection");
  expect(sent("DELETE")[0]).toMatchObject({
    path: "/api/v1/workspaces/ws_1/memories/mem_1",
    ifMatch: '"mem_1:3"',
  });
});

it("shows files and configuration read-only to readers", async () => {
  const { user } = setup("", ["read"]);
  await screen.findByRole("heading", { name: "Handbook" });
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "New file" })).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  await screen.findByRole("heading", { name: "Always loaded files" });
  expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Custom" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete memory" })).toBeNull();
});
