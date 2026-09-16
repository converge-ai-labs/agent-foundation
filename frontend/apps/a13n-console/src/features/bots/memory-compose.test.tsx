import { ApiError } from "../../service-client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { MemoryComposer } from "./memory-compose";
import { MemoryOperations } from "./memory-operations";

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
const source = {
  id: "mdoc_source",
  scope_id: "mscope_source",
  title: "Release plan",
  description: "Original summary",
  text: "Private source detail",
  shared: false,
} as Schema["Document"];
const groups = [
  {
    id: "mscope_source",
    name: "Engineering",
    enabled: true,
    audience: "private",
  },
  { id: "mscope_support", name: "Support", enabled: true, audience: "private" },
  { id: "mscope_dm", name: "Private chat", enabled: true, audience: "direct" },
];
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
  http.GET.mockResolvedValue(response({ items: groups, next_cursor: null }));
  http.POST.mockResolvedValue(response({ ...source, id: "mdoc_new" }));
});
afterEach(cleanup);

async function publicationDraft() {
  setup(
    <MemoryComposer
      account={account}
      scopeId="mscope_source"
      source={source}
      mode="publish"
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Share memory" }));
  const dialog = await screen.findByRole("dialog");
  const text = within(dialog).getByLabelText("Memory content");
  await userEvent.clear(text);
  await userEvent.type(text, "Approved release date only");
  await userEvent.click(
    await within(dialog).findByRole("checkbox", { name: /Support/ }),
  );
  await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  return dialog;
}

it("publishes only the reviewed copy to explicit eligible groups", async () => {
  const dialog = await publicationDraft();
  expect(http.POST).not.toHaveBeenCalled();
  expect(within(dialog).getByText("Approved release date only")).toBeTruthy();
  expect(within(dialog).queryByText("Private source detail")).toBeNull();
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Publish copy" }),
  );
  expect(http.POST).toHaveBeenCalledOnce();
  const [path, request] = http.POST.mock.calls[0];
  expect(path).toMatch(/\/publications$/);
  expect(request.body).toEqual({
    title: source.title,
    description: source.description,
    text: "Approved release date only",
    recipient_scope_ids: ["mscope_support"],
  });
  expect(request.params.header["Idempotency-Key"]).toBeTruthy();
  expect(source.text).toBe("Private source detail");
  expect(http.PATCH).not.toHaveBeenCalled();
});

it("excludes direct conversations from recipient selection", async () => {
  setup(
    <MemoryComposer
      account={account}
      scopeId="mscope_source"
      source={source}
      mode="publish"
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Share memory" }));
  const direct = await screen.findByRole("checkbox", { name: /Private chat/ });
  expect(
    direct.hasAttribute("disabled") ||
      direct.getAttribute("aria-disabled") === "true",
  ).toBe(true);
  expect(screen.queryByRole("checkbox", { name: /Engineering/ })).toBeNull();
});

it("locks an unconfirmed publication draft instead of repeating the write", async () => {
  http.POST.mockRejectedValue(
    new ApiError(
      503,
      "memory_write_unconfirmed",
      "Write outcome unknown",
      {},
      null,
    ),
  );
  const dialog = await publicationDraft();
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Publish copy" }),
  );
  expect(
    await within(dialog).findByText(/Your draft is preserved/),
  ).toBeTruthy();
  expect(within(dialog).getByText("Approved release date only")).toBeTruthy();
  expect(
    within(dialog)
      .getByRole("button", { name: "Publish copy" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    within(dialog)
      .getByRole("button", { name: "Back to draft" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(http.POST).toHaveBeenCalledOnce();
});

it("reuses the command key for an unchanged definitive-failure retry", async () => {
  http.POST.mockRejectedValue(
    new ApiError(
      409,
      "sharing_group_unavailable",
      "Group unavailable",
      {},
      null,
    ),
  );
  const dialog = await publicationDraft();
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Publish copy" }),
  );
  await within(dialog).findByText("Group unavailable");
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Publish copy" }),
  );
  expect(http.POST).toHaveBeenCalledTimes(2);
  expect(http.POST.mock.calls[0][1].params.header["Idempotency-Key"]).toBe(
    http.POST.mock.calls[1][1].params.header["Idempotency-Key"],
  );
});

it("creates a correction as a separate document referencing its source", async () => {
  setup(
    <MemoryComposer
      account={account}
      scopeId="mscope_source"
      source={source}
      mode="correction"
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Add correction" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(
    within(dialog).getByLabelText("Title"),
    "Updated release date",
  );
  await userEvent.type(
    within(dialog).getByLabelText("Memory content"),
    "Release moved to Monday",
  );
  await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Save memory" }),
  );
  expect(http.POST.mock.calls[0][0]).toMatch(/\/documents$/);
  expect(http.POST.mock.calls[0][1].body.correction_of).toBe(source.id);
  expect(http.PATCH).not.toHaveBeenCalled();
});

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
    screen.getByRole("button", { name: "Pending operations" }),
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
