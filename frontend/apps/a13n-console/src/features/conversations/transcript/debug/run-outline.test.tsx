// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import type { Client } from "../../../../service-client";
import type { Schema } from "../../../../shared/api";
import { useRunOutline } from "./outline";
import { RunOutline } from "./run-outline";
import {
  fixtureBranchedSession,
  fixtureForkThread,
  fixtureChildThread,
  fixtureThread,
} from "../fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

beforeEach(() => {
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = fixtureBranchedSession();
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

function Outline({
  thread,
  runId,
}: {
  thread: Schema["ThreadView"];
  runId: string;
}) {
  return <RunOutline outline={useRunOutline(thread, runId)} />;
}

function Location() {
  const { pathname, search } = useLocation();
  return <p>{`${pathname}${search}`}</p>;
}

function show(thread = fixtureThread(), runId = "run_2") {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/start"]}>
        <Outline thread={thread} runId={runId} />
        <Routes>
          <Route path="*" element={<Location />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("draws only the current thread's runs and marks the active Run", async () => {
  show();
  const list = within(await screen.findByRole("navigation", { name: "Runs" }));
  await list.findByText("Write the marker");
  const rows = list.getAllByRole("button");
  expect(rows.map((row) => row.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
  ]);
  expect(rows[1]?.getAttribute("aria-current")).toBe("true");
  expect(rows[0]?.hasAttribute("aria-current")).toBe(false);
});

it.each([
  [fixtureForkThread, "run_fork", "Alternative proposal"],
  [fixtureChildThread, "run_child", "Find prior incidents"],
] as const)(
  "excludes inherited runs from a $origin thread",
  async (thread, runId, text) => {
    show(thread, runId);
    const list = within(
      await screen.findByRole("navigation", { name: "Runs" }),
    );
    await list.findByText(text);
    expect(list.getAllByRole("button")).toHaveLength(1);
    expect(list.queryByText("Review the release")).toBeNull();
    expect(list.queryByText("Root thread")).toBeNull();
  },
);

it("opens a current-thread run that is not on the page", async () => {
  show();
  const list = within(await screen.findByRole("navigation", { name: "Runs" }));
  await list.findByText("Review the release");
  await userEvent.setup().click(list.getAllByRole("button")[0]!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_1?view=debug",
    ),
  ).toBeTruthy();
});
