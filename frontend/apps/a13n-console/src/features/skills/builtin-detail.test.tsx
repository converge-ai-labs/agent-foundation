// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { strToU8, zipSync } from "fflate";
import { createClient } from "../../service-client";
import { SkillsPage } from "./list";
import { BuiltinSkillDetail } from "./builtin-detail";

const { download } = vi.hoisted(() => ({ download: vi.fn() }));
vi.mock("../../shared/download", () => ({ downloadBlob: download }));
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/ws_read",
    workspace: { id: "ws_read" },
    can: () => false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

let client: ReturnType<typeof createClient>;
let cache: QueryClient;
afterEach(() => {
  cleanup();
  client.close();
  cache.clear();
  vi.clearAllMocks();
});

function setup({ direct = false, changed = false, denied = false } = {}) {
  const name = "platform-operations";
  const path = `/api/v1/builtin-skills/${name}`;
  const files = {
    "SKILL.md": strToU8(
      "# Platform guide\n\nRead these instructions. [Agent reference](references/agents.md)",
    ),
    "references/agents.md": strToU8("# Agent reference"),
    "instance/catalog.json": strToU8('{"providers": []}'),
  };
  const zip = zipSync(files);
  const skill = {
    name,
    description: "Operate workspace resources.",
    root: "",
    files: Object.entries(files).map(([path, bytes]) => ({
      path,
      size: bytes.length,
    })),
    package_digest: "a".repeat(64),
    package_size: zip.length,
  };
  const requests: Request[] = [];
  let manifestReads = 0;
  client = createClient({
    baseUrl: "http://localhost",
    auth: { type: "session", csrfToken: "test" },
    maxReadRetries: 0,
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/builtin-skills")
        return Response.json({ items: [skill] });
      if (url.pathname === "/api/v1/skills")
        return Response.json({ items: [], next_cursor: null });
      if (denied)
        return Response.json(
          { error: { code: "forbidden", message: "Workspace access denied" } },
          { status: 403 },
        );
      if (url.pathname === path) {
        manifestReads++;
        return Response.json({
          ...skill,
          package_digest:
            changed && manifestReads > 1
              ? "b".repeat(64)
              : skill.package_digest,
        });
      }
      if (url.pathname === `${path}/content`) {
        if (
          changed &&
          url.searchParams.get("package_digest") === skill.package_digest
        ) {
          return Response.json(
            {
              error: {
                code: "conflict",
                message: "Package changed",
                details: { reason: "package_changed" },
              },
            },
            { status: 409 },
          );
        }
        return new Response(new Uint8Array(zip), {
          headers: { "Content-Type": "application/zip" },
        });
      }
      throw new Error(`Unexpected request: ${request.url}`);
    },
  });
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          `/workspace/ws_read/skills${direct ? `/builtin/${name}` : ""}`,
        ]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceId/skills"
            element={<SkillsPage />}
          />
          <Route
            path="/workspace/:workspaceId/skills/builtin/:name"
            element={<BuiltinSkillDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), requests, zip, skill };
}

it("opens a builtin skill by keyboard and shares file preview and cached download for a read-only member", async () => {
  const { user, requests, zip, skill } = setup();
  await screen.findByRole("link", { name: /platform-operations/ });
  screen.getByRole("link", { name: /platform-operations/ }).focus();
  await user.keyboard("{Enter}");
  await screen.findByRole("heading", { name: "Platform guide" });
  expect(
    screen.getByText("Read-only. Updated with Service releases."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New version" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "More skill actions" }),
  ).toBeNull();
  expect(screen.queryByRole("tab", { name: /Versions/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Source" }));
  expect(await screen.findByText(/# Platform guide/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Preview" }));
  await user.click(screen.getByRole("link", { name: "Agent reference" }));
  await screen.findByRole("heading", { name: "Agent reference" });
  await user.click(screen.getByRole("button", { name: "catalog.json" }));
  await screen.findByText(/providers/);
  await user.click(screen.getByRole("button", { name: "Download ZIP" }));
  await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
  expect(
    new Uint8Array(await download.mock.calls[0]![0].arrayBuffer()),
  ).toEqual(zip);
  expect(download.mock.calls[0]![1]).toBe("platform-operations.zip");
  const downloads = requests.filter((request) =>
    new URL(request.url).pathname.endsWith("/content"),
  );
  expect(downloads).toHaveLength(1);
  expect(new URL(downloads[0].url).searchParams.get("package_digest")).toBe(
    skill.package_digest,
  );
  expect(
    requests.every(
      (request) =>
        request.method === "GET" &&
        request.headers.get("X-Workspace-ID") === "ws_read",
    ),
  ).toBe(true);
  await user.click(screen.getByRole("link", { name: "Skills" }));
  await screen.findByRole("link", { name: /platform-operations/ });
});

it("reloads the manifest when retrying content changed by a deployment update", async () => {
  const { user, requests } = setup({ direct: true, changed: true });
  await screen.findByText("Package changed");
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByRole("heading", { name: "Platform guide" });
  expect(
    requests.some(
      (request) =>
        new URL(request.url).searchParams.get("package_digest") ===
        "b".repeat(64),
    ),
  ).toBe(true);
});

it("shows denied access without requesting package content", async () => {
  const { requests } = setup({ direct: true, denied: true });
  await screen.findByText("Workspace access denied");
  expect(requests).toHaveLength(1);
});
