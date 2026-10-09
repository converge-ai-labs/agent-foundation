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

it("lists only the current thread's runs, excluding children and forks", async () => {
  show();
  const list = await openList(/Run 2 of 2/);
  const items = list.getAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
  ]);
  expect(list.queryByText("Root thread")).toBeNull();
  await userEvent.setup().click(items[0]!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_1?view=debug",
    ),
  ).toBeTruthy();
});

it("steps within the current thread and cannot continue into a child or fork", async () => {
  show();
  await screen.findByRole("button", { name: /Run 2 of 2/ });
  expect(screen.getByRole("button", { name: "Next run" })).toHaveProperty(
    "disabled",
    true,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Previous run" }));
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_1?view=debug",
    ),
  ).toBeTruthy();
});

it("shows a single fork Run without parent runs and disables cross-thread stepping", async () => {
  show(fixtureForkThread, "run_fork");
  const list = await openList(/^Run 1 of 1$/);
  expect(list.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(
    ["Run 1Alternative proposal12s"],
  );
  expect(list.queryByText("Root thread")).toBeNull();
  expect(list.queryByText("Fork")).toBeNull();
  expect(screen.getByRole("button", { name: "Previous run" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByRole("button", { name: "Next run" })).toHaveProperty(
    "disabled",
    true,
  );
});
