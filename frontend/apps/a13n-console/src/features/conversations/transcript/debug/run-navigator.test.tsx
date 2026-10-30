// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router";
import { createClient, type Client } from "../../../../service-client";
import { RunNavigator } from "./run-navigator";
import {
  fixtureBranchedSession,
  fixtureForkThread,
  fixtureThread,
  fixtureRun,
  fixtureRunList,
  fixtureRunPage,
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

it("requests ranked pages for the whole thread and preserves sorting when opening a run outside the chronological window", async () => {
  const requests: URLSearchParams[] = [];
  let refresh: Promise<void> | undefined;
  let finishAnchor!: () => void;
  const anchorRead = new Promise<void>((resolve) => {
    finishAnchor = resolve;
  });
  client.close();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      requests.push(url.searchParams);
      if (!url.searchParams.has("sort")) {
        if (url.searchParams.get("anchor") === "run_3") {
          await anchorRead;
          return Response.json(
            fixtureRunPage([fixtureRun({ id: "run_3" })], {
              total: 3,
              number: 3,
            }),
          );
        }
        return Response.json(
          fixtureRunPage(
            [fixtureRun({ id: "run_2" }), fixtureRun({ id: "run_1" })],
            { total: 3, number: 2, previous_cursor: "newer" },
          ),
        );
      }
      const more = url.searchParams.has("cursor");
      await refresh;
      return Response.json({
        items: [
          {
            ...fixtureRunList(fixtureRun({ id: more ? "run_1" : "run_3" })),
            number: more ? 1 : 3,
            cost: more ? "0.1" : "7",
            model_calls: 18,
            unpriced_requests: 0,
          },
        ],
        total: 3,
        previous_cursor: null,
        next_cursor: more ? null : "rank-2",
      });
    },
  });
  show();
  const panel = await screen.findByRole("navigation", { name: "Runs" });
  const list = within(panel);
  await userEvent
    .setup()
    .click(list.getByRole("button", { name: "Sort by Cost" }));
  await list.findByRole("button", { name: /^Run 3/ });
  expect(
    list
      .getByRole("button", { name: "Sort by Cost, Descending" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(list.getAllByRole("button", { name: /^Run \d/ })).toHaveLength(1);
  // A stream invalidation must keep loaded results and the paging action visible.
  let finishRefresh!: () => void;
  refresh = new Promise<void>((resolve) => {
    finishRefresh = resolve;
  });
  const refetched = cache.invalidateQueries({
    predicate: (query) => query.queryKey.includes("ranked-runs"),
  });
  await waitFor(() => expect(cache.isFetching()).toBeGreaterThan(0));
  expect(list.queryByText("Loading…")).toBeNull();
  expect(list.getByRole("button", { name: "Load more" })).toHaveProperty(
    "disabled",
    false,
  );
  finishRefresh();
  await refetched;
  await userEvent
    .setup()
    .click(list.getByRole("button", { name: "Load more" }));
  await list.findByRole("button", { name: /^Run 1/ });
  const ranked = requests.filter((params) => params.has("sort"));
  expect(
    ranked.map((params) => [
      params.get("sort"),
      params.get("direction"),
      params.get("cursor"),
    ]),
  ).toEqual([
    ["cost", "desc", null],
    ["cost", "desc", null],
    ["cost", "desc", "rank-2"],
  ]);
  expect(ranked.every((params) => !params.has("anchor"))).toBe(true);
  const rows = list.getAllByRole("button", { name: /^Run \d/ });
  let selected = list.getByRole("button", { name: /^Run 3/ });
  panel.scrollTop = 90;
  await userEvent.setup().click(selected);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_3?run_sort=cost&run_direction=desc&view=debug",
    ),
  ).toBeTruthy();
  await waitFor(() =>
    expect(requests.some((params) => params.get("anchor") === "run_3")).toBe(
      true,
    ),
  );
  const expectStable = (number: number) => {
    expect(screen.getByRole("navigation", { name: "Runs" })).toBe(panel);
    expect(list.getAllByRole("button", { name: /^Run \d/ })).toEqual(rows);
    expect(panel.scrollTop).toBe(90);
    expect(document.activeElement).toBe(selected);
    expect(selected.getAttribute("aria-current")).toBe("true");
    expect(
      screen.getByRole("button", { name: new RegExp(`Run ${number} of 3`) }),
    ).toBeTruthy();
  };
  expectStable(3);
  // The list remains interactive, and a late history response cannot restore an old selection.
  selected = list.getByRole("button", { name: /^Run 1/ });
  await userEvent.setup().click(selected);
  expectStable(1);
  await act(async () => finishAnchor());
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expectStable(1);
  expect(requests.filter((params) => params.has("sort"))).toHaveLength(3);
});

it("loads a ranked deep link independently of the chronological window", async () => {
  let finishHistory!: () => void;
  const history = new Promise<void>((resolve) => {
    finishHistory = resolve;
  });
  client.close();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      if (!url.searchParams.has("sort")) await history;
      return Response.json(
        fixtureRunPage([
          fixtureRun({ id: "run_2" }),
          fixtureRun({ id: "run_1" }),
        ]),
      );
    },
  });
  show(fixtureThread(), "run_2", "?run_sort=duration&run_direction=desc");
  const panel = await screen.findByRole("navigation", { name: "Runs" });
  expect(
    within(panel)
      .getByRole("button", { name: /^Run 2/ })
      .getAttribute("aria-current"),
  ).toBe("true");
  expect(screen.getByRole("button", { name: /Run 2 of 2/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Previous run" })).toHaveProperty(
    "disabled",
    true,
  );
  await act(async () => finishHistory());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Previous run" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  expect(screen.getByRole("navigation", { name: "Runs" })).toBe(panel);
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

function Navigator({ thread }: { thread: ReturnType<typeof fixtureThread> }) {
  const { runId = "" } = useParams();
  return (
    <>
      <RunNavigator thread={thread} runId={runId} />
      <Location />
    </>
  );
}

function show(thread = fixtureThread(), runId = "run_2", search = "") {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          `/workspace/design/sessions/${thread.session_id}/threads/${thread.id}/runs/${runId}${search}`,
        ]}
      >
        <Routes>
          <Route
            path="/workspace/design/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={<Navigator thread={thread} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The pill's own list, which the outline in the gutter mirrors. */
async function openList(name: RegExp) {
  await userEvent.setup().click(await screen.findByRole("button", { name }));
  return within(
    (await screen.findAllByRole("navigation", { name: "Runs" })).at(-1)!,
  );
}

it("lists only the current thread's runs, excluding children and forks", async () => {
  show();
  const list = await openList(/Run 2 of 2/);
  const items = list.getAllByRole("button", { name: /^Run \d/ });
  expect(items).toHaveLength(2);
  expect(items[0]!.textContent).toContain("Run 1Review the release");
  expect(items[1]!.textContent).toContain("Run 2Write the marker");
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

it("draws nothing for a lone fork Run, since parent runs are not its own", async () => {
  show(fixtureForkThread, "run_fork");
  // Once the Thread's Runs are read, the fork still has only its own one.
  await waitFor(() => {
    const queries = cache.getQueryCache().getAll();
    expect(queries.length).toBeGreaterThan(0);
    expect(
      queries.find((query) => query.queryKey[2] === "runs")?.state.status,
    ).toBe("success");
  });
  expect(screen.queryByRole("button", { name: "Previous run" })).toBeNull();
});
