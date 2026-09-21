// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import type { Client } from "../../../../service-client";
import { RunNavigator } from "./run-navigator";
import {
  fixtureBranchedSession,
  fixtureForkThread,
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

function Location() {
  const { pathname, search } = useLocation();
  return <p>{`${pathname}${search}`}</p>;
}

function show(thread = fixtureThread(), runId = "run_2") {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/start"]}>
        <RunNavigator thread={thread} runId={runId} />
        <Routes>
          <Route path="*" element={<Location />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The pill's own list, which the outline in the gutter mirrors. */
async function openList(name: RegExp) {
  await userEvent.setup().click(await screen.findByRole("button", { name }));
  return within(await screen.findByRole("menu"));
}

it("lists every run of the thread and of its child threads, and opens one", async () => {
  show();
  const list = await openList(/Run 2 of 2/);
  await list.findByText("Alternative proposal");
  const items = list.getAllByRole("menuitem");
  // Both threads that branched from Run 2 follow it: the delegation, then the
  // fork that reads on from it.
  expect(items.map((item) => item.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
    "Run 1Find prior incidents12s",
    "Run 1Alternative proposal12s",
  ]);
  // The child thread is named by the agent that answers it, under its run.
  expect(list.getByText("Researcher")).toBeTruthy();
  expect(list.queryByText("Child thread")).toBeNull();
  await userEvent.setup().click(items[2]!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_child/runs/run_child?view=debug",
    ),
  ).toBeTruthy();
});

it("steps to the previous run and keeps the debug level", async () => {
  show();
  const user = userEvent.setup();
  await screen.findByRole("button", { name: /Run 2 of 2/ });
  await user.click(screen.getByRole("button", { name: "Previous run" }));
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_1?view=debug",
    ),
  ).toBeTruthy();
  // The child thread's run follows the last run of this thread.
  expect(
    screen.getByRole("button", { name: "Next run" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("nests a fork under the run it branched from, ahead of its own", async () => {
  show(fixtureForkThread, "run_fork");
  const list = await openList(/^Run 1$/);
  const items = await list.findAllByRole("menuitem");
  // One run of its own, but the lineage reaches two more in the root thread.
  expect(items.map((item) => item.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
    "Run 1Alternative proposal12s",
  ]);
  const branch = list.getByText("Fork").parentElement;
  expect(branch?.hasAttribute("data-nested")).toBe(true);
  expect(branch?.previousElementSibling?.textContent).toContain(
    "Write the marker",
  );
  expect(list.getByText("Root thread")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Next run" }).hasAttribute("disabled"),
  ).toBe(true);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Previous run" }));
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_2?view=debug",
    ),
  ).toBeTruthy();
});
