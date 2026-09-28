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
import { createClient } from "../../service-client";
import { SkillDetail } from "./page";
import { strToU8, zipSync } from "fflate";

const { download } = vi.hoisted(() => ({ download: vi.fn() }));
vi.mock("../../shared/download", () => ({ downloadBlob: download }));
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/ws_design",
    workspace: { id: workspaceId },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

let workspaceId: string;
let client: ReturnType<typeof createClient>;
let cache: QueryClient;

afterEach(() => {
  cleanup();
  client?.close();
  cache?.clear();
  vi.clearAllMocks();
});

function setup(workspace: string, search = "") {
  workspaceId = workspace;
  const skill = {
    id: "sk_example",
    workspace_id: workspace,
    name: "Example skill",
    version: 4,
    default_revision_id: "skr_example",
    archived_at: null as string | null,
  };
  const currentFiles = {
    "SKILL.md": strToU8(
      "---\nname: example\ndescription: Example\n---\n# Current instructions\n\nRead the current package.",
    ),
    "references/checklist.md": strToU8("# Current checklist"),
  };
  const olderFiles = {
    "SKILL.md": strToU8("# Earlier instructions"),
    "notes.txt": strToU8("Earlier notes"),
  };
  const makeRevision = (
    id: string,
    number: number,
    root: string,
    files: Record<string, Uint8Array>,
  ) => ({
    id,
    number,
    skill_id: skill.id,
    workspace_id: workspace,
    config: {
      name: "example",
      description: "Example",
      root,
      files: Object.entries(files).map(([path, bytes]) => ({
        path,
        size: bytes.length,
      })),
      source: { kind: "upload", upload_id: "upl_example" },
    },
    created_at: "2026-09-09T00:00:00Z",
  });
  const current = makeRevision("skr_example", 2, "", currentFiles);
  // An uploaded archive may keep its package in one top-level directory.
  const older = makeRevision("skr_older", 1, "example/", olderFiles);
  const zip = zipSync(currentFiles);
  const requests: Request[] = [];
  const skills = `/api/v1/skills`;
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    requests.push(request);
    const route = `${request.method} ${new URL(request.url).pathname}`;
    switch (route) {
      case `GET ${skills}/sk_example`:
        return Response.json(skill, { headers: { ETag: '"skill-v1"' } });
      case `GET ${skills}/sk_example/revisions`:
        return Response.json({ items: [current, older], next_cursor: null });
      case `GET ${skills}/sk_example/revisions/skr_example`:
        return Response.json(current);
      case `GET ${skills}/sk_example/revisions/skr_older`:
        return Response.json(older);
      case `GET ${skills}/sk_example/revisions/skr_example/content`:
        return new Response(new Uint8Array(zip), {
          headers: { "Content-Type": "application/zip" },
        });
      case `GET ${skills}/sk_example/revisions/skr_older/content`:
        return new Response(
          new Uint8Array(
            zipSync(
              Object.fromEntries(
                Object.entries(olderFiles).map(([path, bytes]) => [
                  `example/${path}`,
                  bytes,
                ]),
              ),
            ),
          ),
          { headers: { "Content-Type": "application/zip" } },
        );
      case `POST ${skills}/sk_example/revisions/skr_older/set-default`:
        skill.default_revision_id = "skr_older";
        return Response.json(skill, { headers: { ETag: '"skill-v2"' } });
      case `GET /api/v1/agents`:
        return Response.json({
          items: [
            {
              id: "ap_example",
              name: "Example agent",
              image_url: null,
            },
          ],
          next_cursor: null,
        });
      case `PATCH ${skills}/sk_example`:
        Object.assign(skill, await request.json());
        return Response.json(skill);
      case `POST ${skills}/sk_example/archive`:
        skill.archived_at = "2026-09-10T00:00:00Z";
        return Response.json(skill);
      default:
        return Response.json(
          { error: { code: "not_found", message: "Skill revision not found" } },
          { status: 404 },
        );
    }
  };
  client = createClient({
    baseUrl: "http://localhost",
    auth: { type: "session", csrfToken: "test-csrf" },
    fetch: fetcher,
    maxReadRetries: 0,
  });
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[`/workspace/ws_design/skills/sk_example${search}`]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceId/skills/:skillId"
            element={<SkillDetail />}
          />
          <Route
            path="/workspace/:workspaceId/skills"
            element={<p>Skill collection</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), requests, zip };
}

it.each(["ws_first", "ws_second"])(
  "reads skill details, revisions, references and downloads in %s with session authentication",
  async (workspace) => {
    const { user, requests, zip } = setup(workspace);
    expect(
      await screen.findByRole("heading", { name: "Example skill" }),
    ).toBeTruthy();
    await screen.findByRole("heading", { name: "Current instructions" });
    await user.click(
      screen.getByRole("button", { name: "More skill actions" }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: "Download ZIP" }),
    );
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(
      new Uint8Array(await download.mock.calls[0]![0].arrayBuffer()),
    ).toEqual(zip);
    expect(download.mock.calls[0]![1]).toBe("example-v2.zip");
    await user.click(screen.getByRole("tab", { name: "Used by" }));
    expect(
      (await screen.findByRole("link", { name: "Example agent" })).getAttribute(
        "href",
      ),
    ).toBe("/workspace/ws_design/agents/ap_example");
    const skills = `/api/v1/skills`;
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      `${skills}/sk_example`,
      `${skills}/sk_example/revisions/skr_example`,
      `${skills}/sk_example/revisions`,
      `${skills}/sk_example/revisions/skr_example/content`,
      `/api/v1/agents`,
    ]);
    expect(
      requests.every(
        (request) => request.headers.get("X-Workspace-ID") === workspace,
      ),
    ).toBe(true);
    // Only unarchived agents with a revision pinning the skill are listed.
    expect(
      Object.fromEntries(new URL(requests.at(-1)!.url).searchParams),
    ).toEqual({ skill_id: "sk_example", archived: "false" });
    expect(
      requests.every((request) => request.credentials === "same-origin"),
    ).toBe(true);
  },
);

it("renames and archives a skill with its CSRF proof and existing ETag", async () => {
  const { user, requests } = setup("ws_settings");
  await screen.findByRole("heading", { name: "Example skill" });
  await user.click(screen.getByRole("button", { name: "More skill actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Rename skill" }),
  );
  const name = await screen.findByRole("textbox", { name: "Display name" });
  await user.clear(name);
  await user.type(name, "Renamed skill");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByRole("heading", { name: "Renamed skill" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "New version" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "More skill actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Archive skill" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Archive skill" }),
  );
  expect(await screen.findByText("Archived")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New version" })).toBeNull();
  const mutations = requests.filter((request) => request.method !== "GET");
  expect(
    mutations.map(
      (request) => `${request.method} ${new URL(request.url).pathname}`,
    ),
  ).toEqual([
    "PATCH /api/v1/skills/sk_example",
    "POST /api/v1/skills/sk_example/archive",
  ]);
  for (const request of mutations) {
    expect(request.headers.get("If-Match")).toBe('"skill-v1"');
    expect(request.headers.get("X-CSRF-Token")).toBe("test-csrf");
  }
});

it("opens SKILL.md by default, switches files without downloading again, and opens older versions", async () => {
  const { user, requests } = setup("ws_files");
  await screen.findByRole("heading", { name: "Current instructions" });
  expect(screen.queryByRole("tab", { name: "Settings" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "checklist.md" }));
  await screen.findByRole("heading", { name: "Current checklist" });
  expect(
    requests.filter((request) =>
      new URL(request.url).pathname.endsWith("/content"),
    ),
  ).toHaveLength(1);
  await user.click(screen.getByRole("tab", { name: /Versions/ }));
  await user.click(await screen.findByRole("link", { name: "v1" }));
  await screen.findByRole("heading", { name: "Earlier instructions" });
  expect(screen.queryByRole("button", { name: "checklist.md" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "notes.txt" }));
  await screen.findByText("Earlier notes");
  await user.click(screen.getByRole("link", { name: "View default version" }));
  await screen.findByRole("heading", { name: "Current instructions" });
});

it("opens a retained version directly and preserves its full source", async () => {
  const { user } = setup("ws_direct", "?revision=skr_older");
  await screen.findByRole("heading", { name: "Earlier instructions" });
  await user.click(screen.getByRole("button", { name: "Source" }));
  expect(await screen.findByText("# Earlier instructions")).toBeTruthy();
});

it("does not download a revision the skill does not own", async () => {
  const { requests } = setup("ws_foreign", "?revision=skr_foreign");
  await screen.findByText("Skill revision not found");
  expect(
    requests.some((request) =>
      new URL(request.url).pathname.endsWith("/content"),
    ),
  ).toBe(false);
});

it("sets an older version as the default with the workspace and existing ETag", async () => {
  const { user, requests } = setup("ws_default");
  await screen.findByRole("heading", { name: "Example skill" });
  await user.click(screen.getByRole("tab", { name: /Versions/ }));
  const row = (version: string) =>
    within(screen.getByRole("link", { name: version }).closest("div")!);
  expect(row("v2").getByText("Default version")).toBeTruthy();
  await user.click(row("v1").getByRole("button", { name: "Set as default" }));
  const confirm = await screen.findByRole("dialog", { name: "Set as default" });
  await user.click(
    within(confirm).getByRole("button", { name: "Set as default" }),
  );
  await waitFor(() => expect(row("v1").getByText("Default version")));
  expect(
    row("v2").getByRole("button", { name: "Set as default" }),
  ).toBeTruthy();
  const request = requests.find((request) => request.method === "POST")!;
  expect(new URL(request.url).pathname).toBe(
    "/api/v1/skills/sk_example/revisions/skr_older/set-default",
  );
  expect(request.headers.get("If-Match")).toBe('"skill-v1"');
  expect(request.headers.get("X-CSRF-Token")).toBe("test-csrf");
});
