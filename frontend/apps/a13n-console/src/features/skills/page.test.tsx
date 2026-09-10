// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient } from "@converge.ai/a13n";
import { SkillDetail } from "./page";

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

function setup(workspace: string) {
  workspaceId = workspace;
  const skill = {
    id: "sk_example",
    workspace_id: workspace,
    key: "example",
    name: "Example skill",
    version: 1,
    current_revision_id: "skr_example",
  };
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
      case "GET /api/v1/skills/sk_example":
        return Response.json(skill, { headers: { ETag: '"skill-v1"' } });
      case "GET /api/v1/skills/sk_example/revisions":
        return Response.json({
          items: [
            {
              id: "skr_example",
              version: 1,
              imported_from: { kind: "zip_upload" },
              manifest: { skill_name: "example" },
              created_at: "2026-09-09T00:00:00Z",
            },
          ],
        });
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
        return new Response("zip-content", {
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
      <MemoryRouter initialEntries={["/workspace/design/skills/sk_example"]}>
        <Routes>
          <Route
            path="/workspace/:workspaceKey/skills/:skillId"
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
  return { user: userEvent.setup(), requests };
}

it.each(["ws_first", "ws_second"])(
  "reads skill details, revisions, references and downloads in %s with session authentication",
  async (workspace) => {
    const { user, requests } = setup(workspace);
    expect(
      await screen.findByRole("heading", { name: "Example skill" }),
    ).toBeTruthy();
    await user.click(
      await screen.findByRole("button", { name: "Download ZIP" }),
    );
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(await download.mock.calls[0]![0].text()).toBe("zip-content");
    expect(download.mock.calls[0]![1]).toBe("example-v1.zip");
    await user.click(screen.getByRole("tab", { name: "Used by agents" }));
    expect(
      await screen.findByRole("link", { name: "Example agent" }),
    ).toBeTruthy();
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      "/api/v1/skills/sk_example",
      "/api/v1/skills/sk_example/revisions",
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
  await user.click(screen.getByRole("tab", { name: "Settings" }));
  const name = screen.getByRole("textbox", { name: "Display name" });
  await user.clear(name);
  await user.type(name, "Renamed skill");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByRole("heading", { name: "Renamed skill" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Delete skill" }));
  await user.click(await screen.findByRole("button", { name: "Confirm" }));
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
