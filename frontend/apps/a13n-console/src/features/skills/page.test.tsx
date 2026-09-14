// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient } from "@converge.ai/a13n";
import { SkillDetail } from "./page";
import { strToU8, zipSync } from "fflate";

const { download } = vi.hoisted(() => ({ download: vi.fn() }));
vi.mock("../../shared/download", () => ({ downloadBlob: download }));
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
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
    key: "example",
    name: "Example skill",
    version: 2,
    current_revision_id: "skr_example",
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
    version: number,
    files: Record<string, Uint8Array>,
  ) => ({
    id,
    version,
    skill_id: skill.id,
    workspace_id: workspace,
    imported_from: { kind: "zip" },
    manifest: {
      skill_name: "example",
      files: Object.entries(files).map(([path, bytes]) => ({
        path,
        size_bytes: bytes.length,
        sha256: "fixture",
      })),
    },
    created_at: "2026-09-09T00:00:00Z",
  });
  const current = makeRevision("skr_example", 2, currentFiles);
  const older = makeRevision("skr_older", 1, olderFiles);
  const zip = zipSync(currentFiles);
  const requests: Request[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    requests.push(request);
    if (request.headers.get("X-A13N-Workspace-ID") !== workspace)
      return Response.json(
        {
          error: { code: "resource_not_found", message: "Workspace required" },
        },
        { status: 404 },
      );
    const route = `${request.method} ${new URL(request.url).pathname}`;
    switch (route) {
      case "GET /api/v1/workspaces/" + workspace + "/skills/example":
      case "GET /api/v1/skills/sk_example":
        return Response.json(skill, { headers: { ETag: '"skill-v1"' } });
      case "GET /api/v1/skills/sk_example/revisions":
        return Response.json({ items: [current, older] });
      case "GET /api/v1/skill-revisions/skr_example":
        return Response.json(current);
      case "GET /api/v1/skill-revisions/skr_older":
        return Response.json(older);
      case "GET /api/v1/skill-revisions/skr_foreign":
        return Response.json({ ...older, skill_id: "sk_other" });
      case "GET /api/v1/skills/sk_example/references":
        return Response.json({
          items: [
            {
              agent_id: "agt_example",
              agent_name: "Example agent",
              agent_key: "example-agent",
              agent_revision_id: "agr_example",
            },
          ],
        });
      case "GET /api/v1/skill-revisions/skr_example/content":
        return new Response(new Uint8Array(zip), {
          headers: { "Content-Type": "application/zip" },
        });
      case "GET /api/v1/skill-revisions/skr_older/content":
        return new Response(new Uint8Array(zipSync(olderFiles)), {
          headers: { "Content-Type": "application/zip" },
        });
      case "PATCH /api/v1/skills/sk_example":
        Object.assign(skill, await request.json());
        return Response.json(skill);
      case "DELETE /api/v1/skills/sk_example":
        return new Response(null, { status: 204 });
      default:
        throw new Error(`Unexpected request: ${route}`);
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
        initialEntries={[`/workspace/design/skills/example${search}`]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceKey/skills/:skillKey"
            element={<SkillDetail />}
          />
          <Route
            path="/workspace/:workspaceKey/skills"
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
    await user.click(
      await screen.findByRole("button", { name: "Download ZIP" }),
    );
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(
      new Uint8Array(await download.mock.calls[0]![0].arrayBuffer()),
    ).toEqual(zip);
    expect(download.mock.calls[0]![1]).toBe("example-v2.zip");
    await user.click(screen.getByRole("tab", { name: "Used by agents" }));
    expect(
      await screen.findByRole("link", { name: "Example agent" }),
    ).toBeTruthy();
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      `/api/v1/workspaces/${workspace}/skills/example`,
      "/api/v1/skill-revisions/skr_example",
      "/api/v1/skill-revisions/skr_example/content",
      "/api/v1/skills/sk_example/references",
    ]);
    expect(
      requests.every((request) => request.credentials === "same-origin"),
    ).toBe(true);
  },
);

it("renames and deletes a skill with its workspace, CSRF proof and existing ETag", async () => {
  const { user, requests } = setup("ws_settings");
  await screen.findByRole("heading", { name: "Example skill" });
  await user.click(screen.getByRole("button", { name: "Rename skill" }));
  const name = screen.getByRole("textbox", { name: "Display name" });
  await user.clear(name);
  await user.type(name, "Renamed skill");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByRole("heading", { name: "Renamed skill" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "More skill actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Delete skill" }),
  );
  await user.click(await screen.findByRole("button", { name: "Delete skill" }));
  expect(await screen.findByText("Skill collection")).toBeTruthy();
  const mutations = requests.filter((request) => request.method !== "GET");
  expect(mutations.map((request) => request.method)).toEqual([
    "PATCH",
    "DELETE",
  ]);
  for (const request of mutations) {
    expect(request.headers.get("If-Match")).toBe('"skill-v1"');
    expect(request.headers.get("X-A13N-CSRF-Token")).toBe("test-csrf");
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
  await user.click(screen.getByRole("tab", { name: "Versions" }));
  await user.click(await screen.findByRole("link", { name: "v1" }));
  await screen.findByRole("heading", { name: "Earlier instructions" });
  expect(screen.queryByRole("button", { name: "checklist.md" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "notes.txt" }));
  await screen.findByText("Earlier notes");
  await user.click(screen.getByRole("link", { name: "View current version" }));
  await screen.findByRole("heading", { name: "Current instructions" });
});

it("opens a retained version directly and preserves its full source", async () => {
  const { user } = setup("ws_direct", "?revision=skr_older");
  await screen.findByRole("heading", { name: "Earlier instructions" });
  await user.click(screen.getByRole("tab", { name: "Source" }));
  expect(await screen.findByText("# Earlier instructions")).toBeTruthy();
});

it("does not download a revision belonging to another skill", async () => {
  const { requests } = setup("ws_foreign", "?revision=skr_foreign");
  await screen.findByText("This version does not belong to this skill.");
  expect(
    requests.some((request) =>
      new URL(request.url).pathname.endsWith("/content"),
    ),
  ).toBe(false);
});
