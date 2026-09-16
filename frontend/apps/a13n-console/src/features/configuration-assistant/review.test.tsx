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
  base_agent_version: 7,
  target_conflict: false,
  source: { version: 3 },
  base: { version: 3 },
  current_target: { version: 7 },
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
  http.GET.mockResolvedValue({
    data: draft,
    response: new Response(null, { headers: { ETag: '"v2"' } }),
  });
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
  expect(dialog.getByText('"Current behavior"')).toBeTruthy();
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
  expect(first.body.expected_target_version).toBe(7);
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
