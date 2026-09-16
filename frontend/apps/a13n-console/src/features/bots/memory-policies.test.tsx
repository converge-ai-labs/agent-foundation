import type { BotAccount } from "./account";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryPolicies } from "./memory-policies";
import type { Schema } from "../../shared/api";
const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const account = {
  id: "acct_test",
  memoryVersion: 1,
  memory: { provider_id: "mp_test" },
} as BotAccount;
const groups = [
  { id: "mscope_eng", name: "Engineering", audience: "private", enabled: true },
  { id: "mscope_support", name: "Support", audience: "public", enabled: true },
  {
    id: "mscope_direct",
    name: "Direct conversation",
    audience: "direct",
    enabled: true,
  },
];
const response = (data: unknown) => ({ data, response: new Response() });
let policies: unknown[];
beforeEach(() => {
  vi.resetAllMocks();
  policies = [];
  http.GET.mockImplementation(async (path: string) =>
    response({ items: path.endsWith("memory-scopes") ? groups : policies }),
  );
  http.POST.mockResolvedValue(response({ id: "mshare_created" }));
  http.PUT.mockResolvedValue(response({ id: "mshare_existing" }));
});
afterEach(cleanup);
async function open() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryPolicies account={account} />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Cross-group sharing" }),
  );
  await screen.findByRole("button", { name: "New sharing policy" });
}
it("selects current eligible groups without opting into history or future enrollment and saves only after review", async () => {
  await open();
  await userEvent.click(
    screen.getByRole("button", { name: "New sharing policy" }),
  );
  await userEvent.type(
    screen.getByLabelText("Policy name"),
    "Release knowledge",
  );
  await screen.findByRole("checkbox", { name: /Engineering/ });
  expect(
    screen
      .getByRole("checkbox", { name: "Long-term" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen
      .getByRole("checkbox", { name: "Daily" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  await userEvent.click(
    screen.getByRole("button", {
      name: "Select all currently eligible groups",
    }),
  );
  expect(
    screen
      .getByRole("checkbox", { name: "Include historical memory" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(
    screen
      .getByRole("checkbox", {
        name: "Automatically include newly configured groups",
      })
      .getAttribute("aria-checked"),
  ).toBe("false");
  await userEvent.click(
    screen.getByRole("button", { name: "Review confirmation" }),
  );
  expect(http.POST).not.toHaveBeenCalled();
  expect(screen.getByText("Engineering, Support")).toBeTruthy();
  expect(screen.getByText(/A new save cutoff/)).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm sharing policy" }),
  );
  expect(http.POST.mock.calls[0][1].body).toEqual({
    name: "Release knowledge",
    scope_ids: ["mscope_eng", "mscope_support"],
    kinds: ["long_term"],
    include_history: false,
    enroll_future_groups: false,
    enabled: true,
  });
});
it("preserves saved choices and version when reopening an existing policy", async () => {
  policies = [
    {
      id: "mshare_existing",
      name: "Team policy",
      scope_ids: ["mscope_eng", "mscope_support"],
      kinds: ["daily"],
      include_history: true,
      enroll_future_groups: true,
      enabled: true,
      version: 7,
      future_since: "2026-09-15T00:00:00Z",
      participants: [
        { scope_id: "mscope_eng", joined_at: "2026-09-15T00:00:00Z" },
      ],
    },
  ];
  await open();
  await userEvent.click(
    await screen.findByRole("button", { name: /Team policy/ }),
  );
  expect(
    screen
      .getByRole("checkbox", { name: "Include historical memory" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen
      .getByRole("checkbox", {
        name: "Automatically include newly configured groups",
      })
      .getAttribute("aria-checked"),
  ).toBe("true");
  await userEvent.click(
    screen.getByRole("checkbox", { name: "Enable mutual sharing" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Review confirmation" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm sharing policy" }),
  );
  expect(http.PUT.mock.calls[0][1].body).toEqual({
    name: "Team policy",
    scope_ids: ["mscope_eng", "mscope_support"],
    kinds: ["daily"],
    include_history: true,
    enroll_future_groups: true,
    enabled: false,
    expected_version: 7,
  });
});
