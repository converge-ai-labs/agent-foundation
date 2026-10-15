// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createClient } from "../../service-client";
import type { Schema } from "../../shared/api";
import { ContextWindow, contextFigures } from "./context-window";

let value: Schema["ContextUsage"] | null;
let status = 200;
const scopes: string[] = [];
const client = createClient({
  baseUrl: "https://service.example",
  auth: { type: "session" },
  fetch: async (input, init) => {
    const url = new URL(new Request(input, init).url);
    scopes.push(url.search);
    return Response.json(
      status === 200
        ? value
        : { error: { code: "internal", message: "Usage unavailable" } },
      { status },
    );
  },
});
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? "")),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  status = 200;
  scopes.length = 0;
});
const receipt = {
  run_id: "run",
  record_id: "record",
  input_tokens: 40000,
  output_tokens: 2000,
  context_window_tokens: 200000,
};
function show(compact = true) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <ContextWindow scope={{ thread_id: "thread" }} compact={compact} />
    </QueryClientProvider>,
  );
}
it("shows last-call occupancy and opens details by keyboard without submitting", async () => {
  value = receipt;
  show();
  const trigger = await screen.findByRole("button", {
    name: "Context window: 21%",
  });
  expect(scopes).toEqual(["?thread_id=thread"]);
  trigger.focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByText("Context window")).toBeTruthy();
  expect(screen.getByText("42k / 200k tokens")).toBeTruthy();
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});
it("preserves unknown usage and unknown capacity separately", async () => {
  expect(contextFigures(null)).toEqual({
    used: null,
    capacity: null,
    percent: null,
  });
  expect(contextFigures({ ...receipt, input_tokens: null })).toEqual({
    used: null,
    capacity: 200000,
    percent: null,
  });
  value = { ...receipt, context_window_tokens: null };
  show(false);
  expect(
    await screen.findByText("Context window size is unknown."),
  ).toBeTruthy();
  expect(screen.queryByText("0%")).toBeNull();
});
it("keeps over-capacity counts visible rather than hiding them at 100 percent", () => {
  expect(contextFigures({ ...receipt, input_tokens: 220000 }).percent).toBe(
    111,
  );
});
it("offers retry when context usage cannot be read", async () => {
  value = receipt;
  status = 500;
  show(false);
  expect(await screen.findByText("Usage unavailable")).toBeTruthy();
  status = 200;
  await userEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(await screen.findByText("42k / 200k tokens")).toBeTruthy();
});
