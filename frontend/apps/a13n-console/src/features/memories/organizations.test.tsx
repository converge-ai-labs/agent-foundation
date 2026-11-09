import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { OrganizationCandidate } from "./organization-candidate";
import { MemoryOrganizations } from "./organizations";
import { MemoryAutomation } from "./automation";
import { organizationQueries } from "./organizations-api";

let client: Client;
let writable = true;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/w",
    can: () => writable,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values: Record<string, unknown> = {}) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? name)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  client?.close();
  writable = true;
});
const candidate: Schema["OrganizationCandidate"] = {
  organization_id: "morg_test",
  memory_id: "mem_candidate",
  target_memory_id: "mem_target",
  status: "ready",
  baseline_seq: 1,
  candidate_seq: 2,
  target_seq: 1,
  changes: [{ path: "MEMORY.md", operation: "update" }],
  application: null,
};
const policy: Schema["OrganizationPolicy"] = {
  enabled: false,
  model: "local",
  instructions: "Keep decisions.",
  history: {
    session_ids: ["sess_source"],
    after: "2026-11-01T00:00:00Z",
    max_runs: 100,
  },
  check_interval_seconds: 10,
  cooldown_seconds: 300,
  trigger: { type: "any_change" },
};
function show(
  view: "candidate" | "tasks" | "automation",
  conflict = false,
  snapshot = candidate,
  cached?: Schema["OrganizationCandidate"],
) {
  const writes: { path: string; body: unknown; etag: string | null }[] = [];
  let current = { ...snapshot },
    automation = {
      id: "mem_target",
      version: 1,
      policy,
      status: {
        pending: false,
        active_run_id: null,
        failure: null,
        history_gap: false,
      },
      processed_history_runs: 0,
    };
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "fixture" },
    fetch: async (input, init) => {
      const request = new Request(input, init),
        path = new URL(request.url).pathname;
      if (request.method !== "GET") {
        const text = await request.text(),
          body = text ? JSON.parse(text) : undefined;
        writes.push({ path, body, etag: request.headers.get("If-Match") });
        if (conflict) {
          conflict = false;
          current = { ...current, candidate_seq: 3, target_seq: 4 };
          automation = {
            ...automation,
            version: 2,
            policy: { ...policy, instructions: "Other policy." },
          };
          return Response.json(
            {
              error: { code: "precondition_failed", message: "Review changed" },
            },
            { status: 412 },
          );
        }
        if (path.endsWith("/organization")) {
          automation = { ...automation, version: 3, policy: body };
          return Response.json(automation, { headers: { ETag: '"policy:3"' } });
        }
        if (path.endsWith("/apply"))
          current = { ...current, status: "applied" };
        return Response.json({
          id: "morg_test",
          request: {
            ...body,
            instructions: body?.instructions ?? "Organize files.",
          },
          sources: [],
          candidate_memory_id: "mem_candidate",
          run_id: "run_test",
          thread_id: "thr_test",
          session_id: "sess_test",
        });
      }
      if (path.endsWith("/candidate/diff"))
        return Response.json({
          path: "MEMORY.md",
          diff: "--- before\n+++ after\n@@ -1 +1 @@\n-old\n+decision\n",
          truncated: true,
        });
      if (path.endsWith("/candidate")) return Response.json(current);
      if (path.endsWith("/organization"))
        return Response.json(automation, {
          headers: { ETag: `"policy:${automation.version}"` },
        });
      if (path.endsWith("/models"))
        return Response.json({
          items: [{ key: "local", name: "Local model", enabled: true }],
          next_cursor: null,
        });
      if (path.endsWith("/sessions"))
        return Response.json({
          items: [
            { id: "sess_source", preview: { input_text: "Product decisions" } },
          ],
          next_cursor: null,
        });
      return Response.json({ items: [], next_cursor: null });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  if (cached)
    cache.setQueryData(
      organizationQueries(client, "ws_test").candidate("morg_test").queryKey,
      cached,
    );
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        {view === "candidate" ? (
          <OrganizationCandidate id="morg_test" />
        ) : view === "tasks" ? (
          <MemoryOrganizations
            target={{ kind: "memory", id: "mem_target" }}
            model="local"
          />
        ) : (
          <MemoryAutomation memory={{ id: "mem_target" } as Schema["Memory"]} />
        )}
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { cache, writes, user: userEvent.setup() };
}
it("starts a reopened review from fresh metadata instead of a cached ready snapshot", async () => {
  const { cache, user } = show(
    "candidate",
    false,
    { ...candidate, candidate_seq: 5, target_seq: 4 },
    candidate,
  );
  await screen.findByText("Target memory has changed");
  await user.click(screen.getByRole("button", { name: /Review details/ }));
  expect(screen.getByText("5")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Apply all changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  cache.clear();
});
it("preserves a refused review, blocks blind retry, and reloads only explicitly", async () => {
  const { cache, writes, user } = show("candidate", true);
  await screen.findByText(
    "Diff truncated. Inspect the complete candidate file before applying.",
  );
  await user.click(screen.getByRole("button", { name: "Mark as reviewed" }));
  await user.click(screen.getByRole("button", { name: "Apply all changes" }));
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Apply all changes",
    }),
  );
  await screen.findByText("Review needs attention");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(writes[0].body).toEqual({
    mode: "checked",
    candidate_seq: 2,
    target_seq: 1,
  });
  cache.setQueryData(
    organizationQueries(client, "ws_test").candidate("morg_test").queryKey,
    { ...candidate, candidate_seq: 3, target_seq: 4 },
  );
  expect(
    screen
      .getByRole("button", { name: "Apply all changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "Reviewed" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(writes).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Reload review" }));
  await screen.findByText("Target memory has changed");
  expect(
    screen
      .getByRole("button", { name: "Mark as reviewed" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  expect(screen.queryByText("Review needs attention")).toBeNull();
  cache.clear();
});
it("keeps full replacement out of the main path and confirms deletion consequences", async () => {
  const { cache, writes, user } = show("candidate", false, {
    ...candidate,
    target_seq: 4,
  });
  await screen.findByText("Target memory has changed");
  expect(
    screen
      .getByRole("button", { name: "Apply all changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen.queryByRole("button", { name: "Replace entire target" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: /Other actions/ }));
  await user.click(
    screen.getByRole("button", { name: "Replace entire target" }),
  );
  const dialog = within(screen.getByRole("dialog"));
  expect(
    dialog.getByText(
      "All current target files will be replaced by the reviewed candidate. Target-only files will be deleted.",
    ),
  ).toBeTruthy();
  await user.click(
    dialog.getByRole("button", { name: "Replace entire target" }),
  );
  await waitFor(() =>
    expect(writes[0]?.body).toEqual({
      mode: "replace_all",
      candidate_seq: 2,
      target_seq: 4,
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Apply all changes" }),
    ).toBeNull(),
  );
  cache.clear();
});
it("navigates changed files and keeps review marks separate from whole-candidate application", async () => {
  const { cache, writes, user } = show("candidate", false, {
    ...candidate,
    changes: [
      ...candidate.changes,
      { path: "notes/decisions.md", operation: "create" },
    ],
  });
  await screen.findByRole("navigation", { name: "Changed files" });
  await user.click(
    await screen.findByRole("button", { name: "Mark as reviewed" }),
  );
  expect(screen.getByText("1 of 2 files reviewed")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next file" }));
  expect(
    screen.getByRole("region", { name: "notes/decisions.md" }),
  ).toBeTruthy();
  expect(
    await screen.findByRole("button", { name: "Mark as reviewed" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Apply all changes" }));
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("All 2 changed files")).toBeTruthy();
  expect(dialog.getByText("1 of 2 files reviewed")).toBeTruthy();
  await user.click(dialog.getByRole("button", { name: "Back to review" }));
  expect(writes).toHaveLength(0);
  cache.clear();
});
it.each([
  "generating",
  "failed",
  "applied",
  "discarded",
  "unavailable",
] as const)("does not offer publication for %s candidates", async (status) => {
  const { cache } = show("candidate", false, { ...candidate, status });
  await screen.findByText("Candidate review");
  expect(
    screen.queryByRole("button", { name: "Apply all changes" }),
  ).toBeNull();
  cache.clear();
});
it("starts a candidate from an explicit session selection and opens its ordinary run", async () => {
  const { cache, writes, user } = show("tasks");
  await user.click(
    await screen.findByRole("button", { name: "Organize memory" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Task instructions (optional)" }),
    "Keep corrections.",
  );
  await user.click(
    screen.getByRole("switch", { name: "Include conversation history" }),
  );
  expect(
    screen
      .getByRole("button", { name: "Start organization" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await user.click(
    await screen.findByRole("button", { name: "Select source sessions" }),
  );
  await user.click(
    await screen.findByRole("checkbox", { name: /Product decisions/ }),
  );
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Start organization" }));
  await screen.findByRole("link", { name: "Open run and usage" });
  expect(writes[0]).toMatchObject({
    path: "/api/v1/memory-organizations",
    body: {
      target: { kind: "memory", id: "mem_target" },
      model: "local",
      output: "candidate",
      instructions: "Keep corrections.",
      history: {
        session_ids: ["sess_source"],
        max_runs: 100,
        after: null,
        before: null,
      },
    },
  });
  cache.clear();
});
it("retains a policy draft and its ETag through refresh and a stale save", async () => {
  const { cache, writes, user } = show("automation", true);
  await user.click(
    await screen.findByRole("button", { name: /Automatic policy/ }),
  );
  const field = await screen.findByRole("textbox", {
    name: "Task instructions (optional)",
  });
  await user.clear(field);
  await user.type(field, "My policy.");
  await user.click(screen.getByRole("button", { name: "Save policy" }));
  await screen.findByText("Policy changed");
  expect(field).toHaveProperty("value", "My policy.");
  expect(writes[0]).toMatchObject({
    etag: '"policy:1"',
    body: { instructions: "My policy.", history: policy.history },
  });
  await user.click(
    screen.getByRole("button", { name: "Reload current policy" }),
  );
  await waitFor(() => expect(field).toHaveProperty("value", "Other policy."));
  cache.clear();
});
it("keeps read-only review inspectable without publication controls", async () => {
  writable = false;
  const { cache } = show("candidate");
  await screen.findByRole("link", { name: "Inspect candidate files" });
  expect(
    screen.queryByRole("button", { name: "Apply all changes" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Discard candidate" }),
  ).toBeNull();
  cache.clear();
});
