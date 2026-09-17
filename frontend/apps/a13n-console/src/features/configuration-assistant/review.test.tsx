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
import { afterEach, expect, it, vi } from "vitest";
import { DraftReview } from "./review";
import { initialConfig } from "../agents/configuration";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const draft = {
  id: "cdraft_test",
  version: 2,
  mode: "update",
  status: "open",
  config: initialConfig("Support"),
  target_agent_id: "agt_test",
  base_agent_etag: '"target-v7"',
  target_conflict: false,
  source: { version: 3 },
  base: { version: 3 },
  current_target: { version: 7 },
  current_target_etag: '"target-v7"',
  content_digest: "a".repeat(64),
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
  source_to_candidate: [],
  base_to_candidate: [],
  base_to_current_target: [],
  current_target_to_candidate: [
    {
      path: ["instructions"],
      before: "Current behavior",
      after: "Reviewed behavior",
      before_present: true,
      after_present: true,
    },
  ],
  latest_validation: {
    checked_at: "2026-09-15T10:00:00Z",
    dependency_digest: "b".repeat(64),
    warnings: [],
  },
};

function setup() {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/applications")
      ? { items: [], next_cursor: null }
      : draft,
    response: new Response(null, { headers: { ETag: '"v2"' } }),
  }));
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <DraftReview draftId={draft.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), cache };
}

it("applies the frozen review and current-target diff, retaining the key after a lost response", async () => {
  const { user, cache } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Review and apply" }),
  );
  const dialog = within(
    screen.getByRole("dialog", { name: "Apply reviewed draft" }),
  );
  expect(dialog.getByText("Current behavior")).toBeTruthy();
  expect(
    (
      dialog.getByRole("button", {
        name: "Apply reviewed configuration",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.type(
    dialog.getByLabelText("Reason for applying without verification"),
    "Reviewed for a limited trial",
  );
  cache.setQueryData(["configuration-draft", "ws_test", draft.id], {
    value: { ...draft, version: 3, content_digest: "c".repeat(64) },
    etag: '"v3"',
  });
  http.POST.mockRejectedValue(new Error("Response unavailable"));
  await user.click(
    dialog.getByRole("button", { name: "Apply reviewed configuration" }),
  );
  await dialog.findByText("Response unavailable");
  await user.click(
    dialog.getByRole("button", { name: "Apply reviewed configuration" }),
  );
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(2));
  const first = http.POST.mock.calls[0]?.[1],
    second = http.POST.mock.calls[1]?.[1];
  expect(first.body.expected_version).toBe(2);
  expect(first.body).not.toHaveProperty("expected_target_version");
  expect(first.body.content_digest).toBe("a".repeat(64));
  expect(first.params.header["If-Match"]).toBe('"v2"');
  expect(second.params.header["Idempotency-Key"]).toBe(
    first.params.header["Idempotency-Key"],
  );
});

it("keeps the editor's original precondition when a newer draft is fetched", async () => {
  const { user, cache } = setup();
  await user.click(await screen.findByRole("button", { name: "Edit draft" }));
  const dialog = within(
    screen.getByRole("dialog", { name: "Edit configuration draft" }),
  );
  cache.setQueryData(["configuration-draft", "ws_test", draft.id], {
    value: { ...draft, version: 3 },
    etag: '"v3"',
  });
  http.PATCH.mockRejectedValue(new Error("Draft changed"));
  await user.click(dialog.getByRole("button", { name: "Save draft" }));
  await dialog.findByText("Draft changed");
  expect(http.PATCH.mock.calls[0]?.[1].body.expected_version).toBe(2);
  expect(http.PATCH.mock.calls[0]?.[1].params.header["If-Match"]).toBe('"v2"');
  expect(
    JSON.parse(
      (
        dialog.getByLabelText(
          "Complete configuration JSON",
        ) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual(draft.config);
  expect(http.POST).not.toHaveBeenCalled();
});

it("keeps the same draft editable after application and shows its retained receipt", async () => {
  const { user } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Review and apply" }),
  );
  const dialog = within(
    screen.getByRole("dialog", { name: "Apply reviewed draft" }),
  );
  await user.type(
    dialog.getByLabelText("Reason for applying without verification"),
    "Reviewed changes",
  );
  const receipt = {
    draft_id: draft.id,
    reviewed_version: draft.version,
    reviewed_digest: draft.content_digest,
    agent_id: "agt_test",
    agent_revision_id: "arev_applied",
    agent_revision_version: 8,
    applied_at: draft.updated_at,
    no_change: false,
  };
  const continued = {
    ...draft,
    version: 3,
    base_agent_etag: '"target-v8"',
    latest_validation: null,
    latest_application_receipt: receipt,
    current_target_to_candidate: [],
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/applications")
      ? { items: [receipt], next_cursor: null }
      : path.endsWith("/agents/{agent}")
        ? { id: "agt_test", key: "customer-support", name: "Support" }
        : continued,
    response: new Response(null, { headers: { ETag: '"v3"' } }),
  }));
  http.POST.mockResolvedValue({ data: receipt });
  await user.click(
    dialog.getByRole("button", { name: "Apply reviewed configuration" }),
  );
  await screen.findByRole("heading", { name: "Configuration draft · v3" });
  expect(screen.getByRole("button", { name: "Edit draft" })).toBeTruthy();
  expect(screen.getByText("Application status")).toBeTruthy();
  const agentLink = await screen.findByRole("link", {
    name: "Open agent · v8",
  });
  expect(agentLink.getAttribute("href")).toBe(
    "/workspace/test/agents/customer-support",
  );
  await user.click(screen.getByRole("button", { name: "Application history" }));
  await waitFor(() =>
    expect(
      screen.getAllByRole("link", { name: "Open agent · v8" }),
    ).toHaveLength(2),
  );
  for (const link of screen.getAllByRole("link", { name: "Open agent · v8" })) {
    expect(link.getAttribute("href")).toBe(
      "/workspace/test/agents/customer-support",
    );
  }
  await user.click(screen.getByRole("button", { name: "Application history" }));
  expect(screen.queryByText("arev_applied")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Technical details" }));
  expect(screen.getByText("Agent revision ID")).toBeTruthy();
  expect(screen.getByText("arev_applied")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Draft details" }));
  expect(screen.getByText(draft.id)).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "Review and apply",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

it("rebases against the target ETag reviewed when the editor opened", async () => {
  const { user, cache } = setup();
  await screen.findByRole("button", { name: "Edit draft" });
  cache.setQueryData(["configuration-draft", "ws_test", draft.id], {
    value: { ...draft, target_conflict: true },
    etag: '"v2"',
  });
  await user.click(screen.getByRole("button", { name: "Edit draft" }));
  const dialog = within(
    screen.getByRole("dialog", { name: "Edit configuration draft" }),
  );
  cache.setQueryData(["configuration-draft", "ws_test", draft.id], {
    value: {
      ...draft,
      target_conflict: true,
      current_target_etag: '"target-v8"',
    },
    etag: '"v2"',
  });
  http.POST.mockRejectedValue(new Error("Target changed after review"));
  await user.click(
    dialog.getByRole("button", { name: "Use edited candidate and rebase" }),
  );
  await dialog.findByText("Target changed after review");
  const request = http.POST.mock.calls[0]?.[1];
  expect(request.body.expected_target_etag).toBe('"target-v7"');
  expect(request.params.header["If-Match"]).toBe('"v2"');
});
