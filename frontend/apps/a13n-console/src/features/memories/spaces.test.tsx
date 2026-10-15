import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { MemorySpacesPage } from "./spaces";
import { MemorySpaceDetail } from "./space-detail";
import {
  spaceDraft,
  spaceUpdate,
  validSpaceDraft,
  validSpaceSelection,
} from "./space-form";

let client: Client;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/w",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => client?.close());
const space: Schema["MemorySpace"] = {
  id: "msp_00000000000000000001",
  memory_id: "mem_00000000000000000001",
  name: "Team knowledge",
  description: "Shared working knowledge",
  enabled: true,
  guide: null,
  always_load: ["global/MEMORY.md"],
  organization: {
    enabled: false,
    model: "local",
    check_interval_seconds: 10,
    cooldown_seconds: 300,
    trigger: { type: "any_change" },
  },
  labels: {},
  file_count: 0,
  content_bytes: 0,
  history_bytes: 0,
  version: 1,
  workspace_id: "ws_test",
  organization_id: "org_test",
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-10-14T00:00:00Z",
  updated_at: "2026-10-14T00:00:00Z",
};
it("builds minimal patches and preserves explicit clearing semantics", () => {
  const draft = spaceDraft(space);
  expect(spaceUpdate(space, draft)).toEqual({});
  expect(
    spaceUpdate(space, {
      ...draft,
      guide: { mode: "none", text: "" },
      description: "",
      model: "",
      alwaysLoad: [],
    }),
  ).toEqual({
    guide: "",
    description: null,
    organization: { ...space.organization, model: null },
    always_load: [],
  });
  expect(spaceUpdate({ ...space, guide: "Keep facts." }, draft)).toEqual({
    guide: null,
  });
  expect(validSpaceDraft({ ...draft, automatic: true, model: "" })).toBe(false);
  expect(
    validSpaceDraft({ ...draft, guide: { mode: "custom", text: " " } }),
  ).toBe(false);
  expect(validSpaceSelection({ enabled: true, spaces: [] })).toBe(true);
  const binding = { name: "team", space_id: space.id, access: "read" as const };
  expect(
    validSpaceSelection({
      enabled: true,
      spaces: [binding, { ...binding, space_id: "another" }],
    }),
  ).toBe(false);
  expect(
    validSpaceSelection({
      enabled: true,
      spaces: [{ ...binding, name: "Invalid alias" }],
    }),
  ).toBe(false);
});
function show(route: string, conflict = false) {
  const writes: {
    method: string;
    path: string;
    body: unknown;
    etag: string | null;
  }[] = [];
  let current = { ...space };
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "fixture-csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init),
        path = new URL(request.url).pathname;
      if (request.method !== "GET") {
        const text = await request.text(),
          body = text ? JSON.parse(text) : undefined;
        writes.push({
          method: request.method,
          path,
          body,
          etag: request.headers.get("If-Match"),
        });
        if (request.method === "PATCH") {
          if (conflict) {
            conflict = false;
            return Response.json(
              {
                error: {
                  code: "precondition_failed",
                  message: "Space changed",
                },
              },
              { status: 412 },
            );
          }
          current = { ...current, ...body, version: current.version + 1 };
          return Response.json(current, {
            headers: { ETag: `"${space.id}:${current.version}"` },
          });
        }
        return Response.json(
          path.endsWith("/organizations") ? { pending: true } : current,
        );
      }
      if (path.endsWith(`/memory-spaces/${space.id}`))
        return Response.json(current, {
          headers: { ETag: `"${space.id}:${current.version}"` },
        });
      if (path.endsWith("/memory-spaces"))
        return Response.json({ items: [current], next_cursor: null });
      if (path.endsWith("/sessions"))
        return Response.json({
          items: [
            {
              id: "sess_onboarding",
              preview: { input_text: "Onboarding priorities" },
              memory: { enabled: true },
              updated_at: space.updated_at,
            },
          ],
          next_cursor: null,
        });
      if (path.endsWith("/organization"))
        return Response.json({ pending: false });
      if (path.endsWith("/models"))
        return Response.json({
          items: [{ key: "local", name: "Local model", enabled: true }],
          next_cursor: null,
        });
      if (path.endsWith(`/memories/${space.memory_id}`))
        return Response.json({
          ...current,
          id: space.memory_id,
          kind: "file",
          type: "postgres",
          inherited_guide: "Keep durable facts.",
        });
      return Response.json({ items: [], next_cursor: null });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/w/sessions/:sessionId" element={<SessionLocation />} />
          <Route path="/w/memory-spaces" element={<MemorySpacesPage />} />
          <Route
            path="/w/memory-spaces/:spaceId"
            element={<MemorySpaceDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { writes, cache, user: userEvent.setup() };
}
function SessionLocation() {
  const location = useLocation();
  return <p>{location.pathname + location.search}</p>;
}
it("opens an associated session directly in chat", async () => {
  const { cache, user } = show(`/w/memory-spaces/${space.id}?tab=sessions`);
  await user.click(
    await screen.findByRole("link", { name: "Onboarding priorities" }),
  );
  await screen.findByText("/w/sessions/sess_onboarding?view=chat");
  cache.clear();
});
it("creates a Space, edits its configuration with an ETag, and requests organization", async () => {
  const { writes, cache, user } = show("/w/memory-spaces");
  await screen.findByText("Team knowledge");
  await user.click(screen.getByRole("button", { name: "Create Space" }));
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Team knowledge",
  );
  await user.click(screen.getByRole("button", { name: "Create Space" }));
  await screen.findByRole("tab", { name: "Configuration" });
  expect(writes[0]).toMatchObject({
    method: "POST",
    path: "/api/v1/memory-spaces",
    body: { name: "Team knowledge", description: null },
  });
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  const name = await screen.findByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "Product knowledge");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("heading", { name: "Product knowledge" });
  expect(writes[1]).toMatchObject({
    method: "PATCH",
    etag: `"${space.id}:1"`,
    body: { name: "Product knowledge" },
  });
  await user.click(screen.getByRole("tab", { name: "Organization" }));
  await user.click(await screen.findByRole("button", { name: "Organize now" }));
  await waitFor(() =>
    expect(writes.at(-1)?.path).toBe(
      `/api/v1/memory-spaces/${space.id}/organizations`,
    ),
  );
  cache.clear();
});
it("preserves a configuration draft on conflict and reloads before retry", async () => {
  const { writes, cache, user } = show(
    `/w/memory-spaces/${space.id}?tab=configuration`,
    true,
  );
  const name = await screen.findByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "My draft");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  await screen.findByText("This Space changed");
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveProperty(
    "value",
    "My draft",
  );
  await user.click(
    screen.getByRole("button", {
      name: "Load current version and keep my draft",
    }),
  );
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Save changes" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("heading", { name: "My draft" });
  expect(writes).toHaveLength(2);
  cache.clear();
});

it("saves count and timing policy, retains it across unrelated edits, and switches back to any change", async () => {
  const { writes, cache, user } = show(
    `/w/memory-spaces/${space.id}?tab=configuration`,
  );
  await user.click(
    await screen.findByRole("combobox", { name: "Automatic trigger" }),
  );
  await user.click(
    screen.getByRole("option", { name: "After a number of changes" }),
  );
  for (const [label, value] of [
    ["Changes per round", "12"],
    ["Check interval (seconds)", "60"],
    ["Cooldown (seconds)", "900"],
  ]) {
    const field = screen.getByRole("spinbutton", { name: label });
    await user.clear(field);
    await user.type(field, value);
  }
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
  expect(writes[0]).toMatchObject({
    body: {
      organization: {
        enabled: false,
        model: "local",
        check_interval_seconds: 60,
        cooldown_seconds: 900,
        trigger: { type: "change_count", count: 12 },
      },
    },
  });
  const name = screen.getByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "Updated knowledge");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("heading", { name: "Updated knowledge" });
  expect(writes[1].body).toEqual({ name: "Updated knowledge" });
  await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
  await user.click(screen.getByRole("combobox", { name: "Automatic trigger" }));
  await user.click(
    await screen.findByRole("option", { name: "Any saved change" }),
  );
  expect(
    screen.queryByRole("spinbutton", { name: "Changes per round" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2]).toMatchObject({
    body: {
      organization: {
        check_interval_seconds: 60,
        cooldown_seconds: 900,
        trigger: { type: "any_change" },
      },
    },
  });
  cache.clear();
});

it("validates whole-number timing and threshold boundaries", () => {
  const draft = spaceDraft(space);
  for (const invalid of [
    { checkInterval: "" },
    { checkInterval: "9" },
    { checkInterval: "86401" },
    { checkInterval: "10.5" },
    { cooldown: "-1" },
    { cooldown: "604801" },
    { trigger: "change_count" as const, changeCount: "0" },
    { trigger: "change_count" as const, changeCount: "1000001" },
  ])
    expect(validSpaceDraft({ ...draft, ...invalid })).toBe(false);
  expect(
    validSpaceDraft({ ...draft, cooldown: "0", checkInterval: "86400" }),
  ).toBe(true);
});
