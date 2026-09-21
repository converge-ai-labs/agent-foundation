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
import { fixtureBranchedSession, fixtureThread } from "../fixture";

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
  thread: Schema["ThreadResource"];
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

it("draws the thread's runs with the threads they branched into", async () => {
  show();
  const tree = within(await screen.findByRole("navigation", { name: "Runs" }));
  await tree.findByText("Alternative proposal");
  const rows = tree.getAllByRole("button");
  // Everything Run 2 started hangs off it: the delegation, then the fork.
  expect(rows.map((row) => row.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
    "Run 1Find prior incidents12s",
    "Run 1Alternative proposal12s",
  ]);
  // The delegated thread hangs off the run that dispatched it.
  const branch = tree.getByText("Researcher").parentElement;
  expect(branch?.hasAttribute("data-nested")).toBe(true);
  expect(branch?.previousElementSibling?.textContent).toContain(
    "Write the marker",
  );
  // The tree branches, so the root group names it.
  expect(tree.getByText("Root thread")).toBeTruthy();
  expect(rows[1]?.getAttribute("aria-current")).toBe("true");
  expect(rows[0]?.hasAttribute("aria-current")).toBe(false);
});

it("opens a run that is not on the page", async () => {
  show();
  const tree = within(await screen.findByRole("navigation", { name: "Runs" }));
  await tree.findByText("Find prior incidents");
  await userEvent.setup().click(tree.getAllByRole("button")[2]!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_child/runs/run_child?view=debug",
    ),
  ).toBeTruthy();
});
