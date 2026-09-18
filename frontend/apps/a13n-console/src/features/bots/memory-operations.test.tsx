import type { BotAccount } from "./account";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryOperations } from "./memory-operations";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  DELETE: vi.fn(),
}));
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
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup(component: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(<QueryClientProvider client={cache}>{component}</QueryClientProvider>);
  return cache;
}
beforeEach(() => {
  vi.resetAllMocks();
});
afterEach(cleanup);

it("checks an uncertain operation without adding another document", async () => {
  const entry = {
    id: "mdoc_pending",
    title: "Unconfirmed document",
    state: "unconfirmed",
    version: 1,
  };
  http.GET.mockResolvedValue(response({ items: [entry] }));
  http.POST.mockResolvedValue(response(entry));
  setup(<MemoryOperations account={account} scopeId="mscope_source" />);
  await userEvent.click(
    screen.getByRole("button", { name: "Memory needs attention" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Check result" }),
  );
  expect(
    await screen.findByText(/Do not submit a duplicate document/),
  ).toBeTruthy();
  expect(http.POST.mock.calls[0][0]).toMatch(/\/reconcile$/);
  expect(http.POST.mock.calls[0][1].body).toBeUndefined();
});
