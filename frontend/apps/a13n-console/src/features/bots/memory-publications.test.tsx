import { ApiError } from "@converge.ai/a13n";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { MemoryPublications } from "./memory-publications";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  DELETE: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const account = {
  id: "acct_test",
  memory: { provider_id: "mp_test" },
} as Schema["Account"];
const copy = {
  id: "mdoc_copy",
  title: "Approved release",
  text: "Public release date",
  state: "active",
  version: 1,
};
const groups = [
  { id: "mscope_support", name: "Support", enabled: true, audience: "private" },
  { id: "mscope_product", name: "Product", enabled: true, audience: "private" },
];
let version: number;
let recipients: string[];
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
beforeEach(() => {
  vi.resetAllMocks();
  version = 1;
  recipients = ["mscope_support"];
  http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/audience"))
      return response({ version, recipient_scope_ids: [...recipients] });
    if (path.endsWith("/memory-scopes")) return response({ items: groups });
    if (path.endsWith("/publications")) return response({ items: [copy] });
    return response(copy);
  });
  http.PATCH.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
});
afterEach(cleanup);
async function openCopy() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryPublications account={account} scopeId="mscope_source" />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Published copies" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: /Approved release/ }),
  );
  await screen.findByRole("checkbox", { name: /Support/ });
  return cache;
}

it("preserves the draft's original version across refresh and conflict instead of overwriting a concurrent audience", async () => {
  const cache = await openCopy();
  await userEvent.click(screen.getByRole("checkbox", { name: /Product/ }));
  version = 2;
  recipients = [];
  await cache.invalidateQueries({
    queryKey: ["bot-memory-publication", account.id],
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "Review recipients" }),
  );
  expect(http.PATCH).not.toHaveBeenCalled();
  http.PATCH.mockRejectedValue(
    new ApiError(409, "version_conflict", "Concurrent change", {}, null),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm recipients" }),
  );
  await screen.findByText("This resource changed");
  expect(http.PATCH.mock.calls[0][1].body).toEqual({
    expected_version: 1,
    recipient_scope_ids: ["mscope_support", "mscope_product"],
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Discard recipient changes" }),
  );
  expect(
    screen
      .getByRole("checkbox", { name: /Support/ })
      .getAttribute("aria-checked"),
  ).toBe("false");
  await userEvent.click(screen.getByRole("checkbox", { name: /Product/ }));
  await userEvent.click(
    screen.getByRole("button", { name: "Review recipients" }),
  );
  http.PATCH.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm recipients" }),
  );
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(2));
  expect(http.PATCH.mock.calls[1][1].body).toEqual({
    expected_version: 2,
    recipient_scope_ids: ["mscope_product"],
  });
});

it("withdraws only the selected copy after explicit confirmation and refreshes memory visibility", async () => {
  const cache = await openCopy();
  const refresh = vi.spyOn(cache, "invalidateQueries");
  expect(screen.getByText("Public release date")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Withdraw copy" }));
  const dialog = screen.getByRole("dialog", { name: "Withdraw copy" });
  expect(http.POST).not.toHaveBeenCalled();
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Withdraw copy" }),
  );
  await waitFor(() => expect(refresh).toHaveBeenCalled());
  expect(http.POST.mock.calls[0][0]).toMatch(
    /\/publications\/\{document_id\}\/withdraw$/,
  );
  expect(http.POST.mock.calls[0][1]).toEqual({
    params: {
      path: {
        account_id: account.id,
        scope_id: "mscope_source",
        document_id: copy.id,
      },
    },
    body: { expected_version: 1 },
  });
  expect(http.DELETE).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(
      screen.queryByRole("region", { name: "Published copy details" }),
    ).toBeNull(),
  );
});
