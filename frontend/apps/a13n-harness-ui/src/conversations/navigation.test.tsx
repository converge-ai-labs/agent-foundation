// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { ConversationNavigation } from "./navigation";
import { useLiveWorkbench } from "../shell/presence";
import { watchSummary } from "../transport/events";
import { IDBFactory } from "fake-indexeddb";
import { ResultTracker, ResultsContext } from "./results";
import type { Schema } from "../transport/client";
import { UnsentProvider, useTrackUnsent } from "./unsent";
import { ThreadDraft } from "./draft";

vi.mock("../transport/events", () => ({ watchSummary: vi.fn(() => () => {}) }));

const projects = [
  { project_id: "project-one", name: "One", roots: ["/one"], position: 0 },
  { project_id: "project-two", name: "Two", roots: ["/two"], position: 1 },
];
function thread(id: string, project: string | null = "project-one") {
  return {
    thread_id: id,
    title: id,
    configuration: { project_id: project },
    root_activity: { state: "inactive" },
    archived: false,
  };
}
function page(
  ids: string[],
  next: string | null = null,
  project: string | null = "project-one",
) {
  return {
    active_rows: activeThreads
      .filter((item) => item.configuration.project_id === project)
      .map((item) => ({ thread: item, project_name: project ?? "No project" })),
    rows: ids.map((id) => ({
      thread: thread(id, project),
      project_name: project ?? "No project",
    })),
    next_cursor: next,
    total: 7,
  };
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
let unsentDrafts: Schema<"DraftSummary">[];
let unsentThreads: ReturnType<typeof thread>[];
let failDrafts: boolean;
let activity: URL[];
let activeThreads: ReturnType<typeof thread>[];
let writes: Request[];
let failMore: boolean;
let failSave: boolean;
let cwd: string;
let pauseMore: Promise<void> | null;
let pageAborted: boolean;
let recentTitle: string;
let queryClient: QueryClient;
beforeEach(() => {
  localStorage.clear();
  unsentDrafts = [];
  unsentThreads = [];
  failDrafts = false;
  activity = [];
  activeThreads = [];
  writes = [];
  failMore = false;
  failSave = false;
  cwd = "/outside";
  pauseMore = null;
  pageAborted = false;
  recentTitle = "Recent 1";
  vi.mocked(watchSummary).mockClear();
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/drafts")
        return failDrafts
          ? json({ error: { message: "Draft discovery unavailable" } }, 503)
          : json(unsentDrafts);
      if (
        url.pathname === "/api/threads/activity/lookup" &&
        unsentThreads.length
      ) {
        const body = await request.json();
        return json(
          unsentThreads
            .filter((item) => body.thread_ids.includes(item.thread_id))
            .map((item) => ({ thread: item, project_name: "One" })),
        );
      }
      if (request.method === "GET") {
        const unsent = unsentThreads.find(
          (item) => url.pathname === `/api/threads/${item.thread_id}`,
        );
        if (unsent) return json({ thread: unsent });
      }
      if (request.method !== "GET") {
        writes.push(request.clone());
        if (request.method === "PUT") {
          if (failSave)
            return json(
              {
                error: { message: "Invalid server directory", code: "invalid" },
              },
              400,
            );
          return json({ source_digest: "saved" });
        }
        if (url.pathname.endsWith("configuration-preview"))
          return json({
            configuration: {
              agent_source: { id: "agent-main" },
              environment_profile_id: "environment-native",
            },
            provenance: {},
          });
        return json(thread("created"));
      }
      if (url.pathname === "/api/projects") return json(projects);
      if (url.pathname === "/api/setup")
        return json({ suggested_project_path: cwd });
      if (url.pathname === "/api/status")
        return json({ features: { host_files: false } });
      if (url.pathname === "/api/configuration/sources")
        return json({ sources: [] });
      if (url.pathname === "/api/selectors")
        return json({ agents: [], environments: [] });
      const activeThread = activeThreads.find(
        (item) =>
          url.pathname === `/api/threads/${encodeURIComponent(item.thread_id)}`,
      );
      if (activeThread) return json({ thread: activeThread });
      if (url.pathname === "/api/threads/selected-old")
        return json({ thread: thread("selected-old") });
      if (url.pathname === "/api/threads/activity") {
        activity.push(url);
        if (url.searchParams.get("query"))
          return json(page(["Global match"], null, "project-two"));
        if (url.searchParams.get("cursor")) {
          request.signal.addEventListener("abort", () => {
            pageAborted = true;
          });
          if (pauseMore) await pauseMore;
          if (failMore)
            return json(
              { error: { message: "Page temporarily unavailable" } },
              503,
            );
          return json(page(["Older one", "Older two"]));
        }
        if (url.searchParams.get("project_id") === "project-one")
          return json(
            page(
              [recentTitle, "Recent 2", "Recent 3", "Recent 4", "Recent 5"],
              "one-next",
            ),
          );
        if (url.searchParams.get("project_id") === "project-two")
          return json(page(["Other project"], null, "project-two"));
        if (url.searchParams.get("project_scope") === "unavailable")
          return json(page(["Missing project task"], null, "project-missing"));
        return json(page(["Projectless task"], null, null));
      }
      throw new Error(`Unexpected request: ${request.method} ${url}`);
    }),
  );
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
});
afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});
function LiveNavigation() {
  const live = useLiveWorkbench(
    { display_name: "Test", color: "#000000" },
    false,
    () => {},
  );
  return <ConversationNavigation presence={live.presence} />;
}
function Location() {
  const location = useLocation();
  return (
    <output aria-label="Current route">
      {location.pathname}
      {location.search}
    </output>
  );
}
function TrackDraft({ draft }: { draft: ThreadDraft }) {
  useTrackUnsent("old-draft", draft);
  return null;
}
function mount(
  path = "/",
  live = false,
  results: ResultTracker | null = null,
  draft?: ThreadDraft,
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter initialEntries={[path]}>
          <ResultsContext value={results}>
            <UnsentProvider>
              {draft && <TrackDraft draft={draft} />}
              {live ? <LiveNavigation /> : <ConversationNavigation />}
            </UnsentProvider>
          </ResultsContext>
          <Location />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
const groupNames = () =>
  [...document.querySelectorAll("[data-project-key]")].map((element) =>
    element.getAttribute("aria-label"),
  );

it("lazily fetches five rows per expanded project and preserves each page through collapse and global search", async () => {
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "One" }));
  await screen.findByText("Recent 5");
  expect(activity).toHaveLength(1);
  expect(activity[0].searchParams.get("limit")).toBe("5");
  fireEvent.click(screen.getByRole("button", { name: "Two" }));
  await screen.findByText("Other project");
  fireEvent.click(
    screen.getByRole("button", { name: "Show more conversations in One" }),
  );
  await screen.findByText("Older two");
  expect(
    activity.filter(
      (url) => url.searchParams.get("project_id") === "project-two",
    ),
  ).toHaveLength(1);
  expect(activity.at(-1)?.searchParams.get("cursor")).toBe("one-next");
  fireEvent.click(screen.getByRole("button", { name: "One" }));
  expect(screen.queryByRole("link", { name: "Older two" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "One" }));
  expect(screen.getByRole("link", { name: "Older two" })).toBeTruthy();
  const calls = activity.length;
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "match" },
  });
  await screen.findByText("Global match");
  expect(activity).toHaveLength(calls + 1);
  expect(activity.at(-1)?.searchParams.has("project_id")).toBe(false);
  expect(screen.queryByRole("button", { name: "Reorder One" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  expect(screen.getByRole("link", { name: "Older two" })).toBeTruthy();
  expect(activity).toHaveLength(calls + 1);
});

it("locates a deep-linked selected row without fetching preceding pages and preserves explicit collapse", async () => {
  mount("/threads/selected-old");
  await screen.findByText("Recent 5");
  expect(
    screen
      .getByRole("link", { name: "selected-old" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.queryByText("Selected conversation · outside this page"),
  ).toBeNull();
  expect(activity).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "One" }));
  await act(() =>
    queryClient.invalidateQueries({ queryKey: ["thread", "selected-old"] }),
  );
  expect(
    screen.getByRole("button", { name: "One" }).getAttribute("aria-expanded"),
  ).toBe("false");
});

it("keeps failed pagination local, retries it, and queries projectless and unavailable groups explicitly", async () => {
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "One" }));
  await screen.findByText("Recent 5");
  failMore = true;
  fireEvent.click(
    screen.getByRole("button", { name: "Show more conversations in One" }),
  );
  await screen.findByText("Page temporarily unavailable");
  expect(screen.getByText("Recent 5")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Two" }));
  await screen.findByText("Other project");
  failMore = false;
  fireEvent.click(
    screen.getByRole("button", { name: "Show more conversations in One" }),
  );
  await screen.findByText("Older two");
  fireEvent.click(screen.getByRole("button", { name: "Without a project" }));
  await screen.findByText("Projectless task");
  expect(activity.at(-1)?.searchParams.get("project_scope")).toBe(
    "projectless",
  );
  fireEvent.click(screen.getByRole("button", { name: "Unavailable projects" }));
  await screen.findByText("Missing project task");
  expect(activity.at(-1)?.searchParams.get("project_scope")).toBe(
    "unavailable",
  );
  expect(
    activity.every(
      (url) => url.searchParams.get("include_archived") === "false",
    ),
  ).toBe(true);
  expect(
    screen.queryByRole("checkbox", { name: "Include archived" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();
});

it("reorders whole project groups with keyboard, cancels preview, and persists only in this browser", async () => {
  const first = mount();
  const handle = await screen.findByRole("button", { name: "Reorder One" });
  fireEvent.keyDown(handle, { key: " " });
  fireEvent.keyDown(handle, { key: "ArrowDown" });
  expect(groupNames()).toEqual(["Two", "One"]);
  fireEvent.keyDown(handle, { key: "Escape" });
  expect(groupNames()).toEqual(["One", "Two"]);
  expect(localStorage.getItem("a13n-harness-ui.project-order")).toBeNull();
  fireEvent.keyDown(handle, { key: " " });
  fireEvent.keyDown(handle, { key: "ArrowDown" });
  fireEvent.keyDown(handle, { key: "Enter" });
  expect(
    JSON.parse(localStorage.getItem("a13n-harness-ui.project-order")!),
  ).toEqual(["project-two", "project-one"]);
  expect(writes).toHaveLength(0);
  first.unmount();
  mount();
  await screen.findByRole("button", { name: "Reorder One" });
  expect(groupNames()).toEqual(["Two", "One"]);
});

it("uses the existing validated source publication to add a project without creating a thread", async () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Add project" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(
    within(dialog).getByRole("textbox", { name: "Project name" }),
    { target: { value: "Example: workspace" } },
  );
  fireEvent.change(
    within(dialog).getByRole("textbox", { name: "Server directory" }),
    { target: { value: "/srv/my project" } },
  );
  failSave = true;
  fireEvent.click(within(dialog).getByRole("button", { name: "Add project" }));
  await screen.findByText("Invalid server directory");
  expect(
    (
      within(dialog).getByRole("textbox", {
        name: "Project name",
      }) as HTMLInputElement
    ).value,
  ).toBe("Example: workspace");
  failSave = false;
  fireEvent.click(within(dialog).getByRole("button", { name: "Add project" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(writes).toHaveLength(2);
  expect(
    writes.every(
      (request) =>
        request.method === "PUT" &&
        decodeURIComponent(new URL(request.url).pathname).startsWith(
          "/api/configuration/sources/projects/",
        ),
    ),
  ).toBe(true);
  expect(writes[0].url).toBe(writes[1].url);
  const document = parse((await writes[1].json()).content);
  expect(document).toMatchObject({
    kind: "project",
    name: "Example: workspace",
    roots: [{ path: "/srv/my project" }],
    defaults: {},
  });
  expect(document.position).toBeUndefined();
});

it("opens a local blank conversation in the selected project without a creation dialog or write", async () => {
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "New conversation in Two" }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByLabelText("Current route").textContent).toBe(
    "/new?project=project-two",
  );
  expect(writes).toHaveLength(0);
});

it("ignores corrupt or stale browser ordering without hiding newly added projects", async () => {
  localStorage.setItem(
    "a13n-harness-ui.project-order",
    JSON.stringify(["project-removed", "project-two", "project-two", 42]),
  );
  localStorage.setItem("a13n-harness-ui.project-expansion", "invalid json");
  mount();
  await screen.findByRole("button", { name: "Reorder One" });
  expect(groupNames()).toEqual(["Two", "One"]);
});

it("retains keyboard focus when a project DOM group moves", async () => {
  mount();
  const handle = await screen.findByRole("button", { name: "Reorder One" });
  handle.focus();
  fireEvent.keyDown(handle, { key: " " });
  fireEvent.keyDown(handle, { key: "ArrowDown" });
  expect(document.activeElement).toBe(handle);
  fireEvent.keyDown(handle, { key: "Enter" });
  expect(groupNames()).toEqual(["Two", "One"]);
});

it("captures pointer drags on the stable scroller and never turns a click into a move", async () => {
  mount();
  const handle = await screen.findByRole("button", { name: "Reorder One" });
  const scroller = document.querySelector<HTMLElement>(
    "[data-project-scroll]",
  )!;
  scroller.setPointerCapture = vi.fn();
  const target = document.querySelector<HTMLElement>(
    '[data-project-key="project-two"]',
  )!;
  Object.defineProperty(document, "elementFromPoint", {
    configurable: true,
    value: () => target,
  });
  const point = (
    element: HTMLElement,
    kind: "pointerDown" | "pointerMove" | "pointerUp",
    x: number,
    y: number,
  ) => {
    const event = new MouseEvent(
      kind.replace(/[A-Z]/, (letter) => letter.toLowerCase()),
      { bubbles: true, button: 0, clientX: x, clientY: y },
    );
    Object.defineProperty(event, "pointerId", { value: 1 });
    fireEvent(element, event);
  };
  point(handle, "pointerDown", 10, 10);
  point(scroller, "pointerMove", 12, 11);
  point(scroller, "pointerUp", 12, 11);
  expect(localStorage.getItem("a13n-harness-ui.project-order")).toBeNull();
  point(handle, "pointerDown", 10, 10);
  expect(scroller.setPointerCapture).toHaveBeenCalledWith(1);
  point(scroller, "pointerMove", 50, 100);
  expect(groupNames()).toEqual(["Two", "One"]);
  point(scroller, "pointerUp", 50, 100);
  expect(
    JSON.parse(localStorage.getItem("a13n-harness-ui.project-order")!),
  ).toEqual(["project-two", "project-one"]);
  expect(writes).toHaveLength(0);
});

it("does not cancel an in-flight Show more when a summary event refreshes navigation", async () => {
  mount("/", true);
  fireEvent.click(await screen.findByRole("button", { name: "One" }));
  await screen.findByText("Recent 5");
  let release!: () => void;
  pauseMore = new Promise<void>((resolve) => {
    release = resolve;
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Show more conversations in One" }),
  );
  await waitFor(() =>
    expect(activity.at(-1)?.searchParams.get("cursor")).toBe("one-next"),
  );
  recentTitle = "Renamed during pagination";
  act(() => {
    vi.mocked(watchSummary).mock.calls.at(-1)![1]();
    vi.mocked(watchSummary).mock.calls.at(-1)![1]();
  });
  expect(pageAborted).toBe(false);
  await act(async () => release());
  await screen.findByText("Older two");
  await screen.findByText("Renamed during pagination");
  expect(screen.queryByText("Recent 1")).toBeNull();
  expect(screen.getByText("Older two")).toBeTruthy();
  expect(activity).toHaveLength(4); // first + next, then one coalesced two-page refresh
  expect(pageAborted).toBe(false);
});

it("preserves project scope filtering and cached independent pages through scoped search", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "One" }));
  await screen.findByText("Recent 5");
  await user.click(
    screen.getByRole("button", { name: "Show more conversations in One" }),
  );
  await screen.findByText("Older two");
  await user.click(screen.getByRole("button", { name: "Two" }));
  await screen.findByText("Other project");
  await user.click(screen.getByRole("button", { name: "Filter by project" }));
  await user.click(
    await screen.findByRole("combobox", { name: "Project scope" }),
  );
  await user.click(await screen.findByRole("option", { name: "Two" }));
  expect(screen.queryByRole("button", { name: "One" })).toBeNull();
  expect(screen.getByRole("link", { name: "Other project" })).toBeTruthy();
  fireEvent.change(
    screen.getByRole("searchbox", { name: "Find conversations" }),
    { target: { value: "match" } },
  );
  await screen.findByText("Global match");
  expect(activity.at(-1)?.searchParams.get("project_id")).toBe("project-two");
  expect(screen.getByText("Results from Two")).toBeTruthy();
  fireEvent.change(
    screen.getByRole("searchbox", { name: "Find conversations" }),
    { target: { value: "" } },
  );
  const calls = activity.length;
  await user.click(screen.getByRole("button", { name: "Filter by project" }));
  await user.click(
    await screen.findByRole("combobox", { name: "Project scope" }),
  );
  await user.click(await screen.findByRole("option", { name: "All Projects" }));
  expect(screen.getByRole("link", { name: "Older two" })).toBeTruthy();
  expect(activity).toHaveLength(calls);
});

it("preserves status labels, title tooltips and independent action menus from the polished navigation", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = new URL(input instanceof Request ? input.url : input);
    if (url.pathname === "/api/projects") return json(projects);
    if (url.pathname === "/api/threads/activity")
      return json({
        rows: [
          { thread: thread("Review configuration"), pending_decision: true },
          {
            thread: {
              ...thread("Implement settings"),
              root_activity: { state: "running" },
            },
          },
          {
            thread: thread("Check provider"),
            latest_operation: { status: "failed" },
          },
        ],
        next_cursor: null,
      });
    throw new Error(`Unexpected request: ${url}`);
  });
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "One" }));
  expect(
    await screen.findByRole("link", {
      name: /Review configuration.*Needs your answer/,
    }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Implement settings.*Running/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Check provider.*Failed/ }),
  ).toBeTruthy();
  expect(screen.getByTitle("Review configuration")).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Actions for Review configuration" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Rename conversation" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("menuitem", { name: "Share conversation" }),
  ).toBeTruthy();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Actions for One" }));
  expect(
    await screen.findByRole("menuitem", { name: "Project settings" }),
  ).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(
    screen.getByRole("button", { name: "One" }).getAttribute("aria-expanded"),
  ).toBe("true");
});

it("does not claim empty search results when the list fails", async () => {
  mount();
  await screen.findByRole("button", { name: "One" });
  vi.mocked(fetch).mockResolvedValue(
    json({ error: { message: "Conversations unavailable" } }, 503),
  );
  fireEvent.change(
    screen.getByRole("searchbox", { name: "Find conversations" }),
    { target: { value: "missing" } },
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Conversations unavailable",
  );
  expect(screen.queryByText("No matching conversations.")).toBeNull();
  expect(screen.queryByText("No conversations yet")).toBeNull();
});

it("keeps rows, title and DOM identity across a failed background refresh", async () => {
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "One" }));
  const original = await screen.findByRole("link", { name: "Recent 1" });
  const scroller = document.querySelector<HTMLElement>(
    "[data-project-scroll]",
  )!;
  scroller.scrollTop = 40;
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  let release!: () => void;
  const pause = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const request = input as Request;
    if (request.url.includes("/api/threads/activity")) {
      await pause;
      return json({ error: { message: "Archive filter unavailable" } }, 503);
    }
    return originalFetch(input, init);
  });
  act(() => {
    void queryClient.invalidateQueries({ queryKey: ["threads"] });
  });
  expect(screen.getByRole("link", { name: "Recent 1" })).toBe(original);
  expect(screen.queryByLabelText("Loading conversations")).toBeNull();
  expect(screen.getByTitle("One").textContent).toBe("One");
  expect(scroller.scrollTop).toBe(40);
  await act(async () => release());
  await screen.findByText("Archive filter unavailable");
  expect(screen.getByRole("link", { name: "Recent 1" })).toBe(original);
});

it("never mixes archived rows into ordinary navigation", async () => {
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  let pause: Promise<void> | null = null;
  let release!: () => void;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const request = input as Request;
    if (request.url.includes("/api/threads/activity")) {
      if (pause) await pause;
      return json({
        rows: [
          { thread: thread("Active") },
          { thread: { ...thread("Archived"), archived: true } },
        ],
        next_cursor: null,
      });
    }
    return originalFetch(input, init);
  });
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "One" }));
  await screen.findByRole("link", { name: "Active" });
  expect(screen.queryByRole("link", { name: /^Archived/ })).toBeNull();
  const active = screen.getByRole("link", { name: "Active" });
  pause = new Promise<void>((resolve) => {
    release = resolve;
  });
  act(() => {
    void queryClient.invalidateQueries({ queryKey: ["threads"] });
  });
  expect(screen.queryByRole("link", { name: /^Archived/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Active" })).toBe(active);
  await act(async () => release());
});

it("targets execution refreshes without invalidating settings or native queries", async () => {
  queryClient.setQueryData(["sources"], { sources: [] });
  queryClient.setQueryData(["native", "files"], []);
  queryClient.setQueryData(["thread", "thread-other", "detail"], {});
  queryClient.setQueryData(["thread", "thread-changed", "detail"], {});
  mount("/", true);
  await screen.findByRole("button", { name: "One" });
  act(() =>
    vi.mocked(watchSummary).mock.calls.at(-1)![1]({
      kind: "root_operation",
      root_thread_id: "thread-changed",
      thread_id: "thread-changed",
      epoch: "epoch",
      sequence: 1,
    }),
  );
  expect(queryClient.getQueryState(["sources"])?.isInvalidated).toBe(false);
  expect(queryClient.getQueryState(["native", "files"])?.isInvalidated).toBe(
    false,
  );
  expect(
    queryClient.getQueryState(["thread", "thread-other", "detail"])
      ?.isInvalidated,
  ).toBe(false);
  await waitFor(() =>
    expect(
      queryClient.getQueryState(["thread", "thread-changed", "detail"])
        ?.isInvalidated,
    ).toBe(true),
  );
});

it("puts the current server directory first by default and on reset, while respecting explicit browser order", async () => {
  cwd = "/two";
  const user = userEvent.setup();
  const view = mount();
  await waitFor(() => expect(groupNames()).toEqual(["Two", "One"]));
  expect(writes).toHaveLength(0);
  view.unmount();
  localStorage.setItem(
    "a13n-harness-ui.project-order",
    JSON.stringify(["project-one", "project-two"]),
  );
  mount();
  await screen.findByRole("button", { name: "Reorder One" });
  expect(groupNames()).toEqual(["One", "Two"]);
  await user.click(screen.getByRole("button", { name: "Actions for One" }));
  await user.click(
    await screen.findByRole("menuitem", {
      name: "Reset project order in this browser",
    }),
  );
  expect(groupNames()).toEqual(["Two", "One"]);
  expect(writes).toHaveLength(0);
});

it("opens project rename directly from the secondary menu without expanding or navigating", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(
    await screen.findByRole("button", { name: "Actions for One" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Rename project" }),
  );
  await screen.findByRole("dialog", { name: "Rename project" });
  expect(
    screen
      .getByRole("button", { name: "One", hidden: true })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(activity).toHaveLength(0);
  expect(writes).toHaveLength(0);
});

it("shows every active conversation before five recent rows and reconciles completion without duplicates", async () => {
  activeThreads = Array.from({ length: 6 }, (_, index) => ({
    ...thread(`Active ${index}`),
    root_activity: { state: index === 0 ? "preparing" : "running" },
  }));
  mount("/threads/Active%200");
  const project = await screen.findByRole("region", { name: "One" });
  await within(project).findByText("Running · 6");
  const runningLinks = () =>
    within(project)
      .getAllByRole("link")
      .filter((link) => /Running|Preparing/.test(link.textContent ?? ""));
  const selectedRow = within(project).getByRole("link", { name: /Active 0/ });
  expect(runningLinks().map((link) => link.textContent)).toEqual(
    activeThreads.map((item) => expect.stringContaining(item.title)),
  );
  expect(within(project).getAllByRole("link")).toHaveLength(11);
  expect(within(project).queryByText(/outside this page/)).toBeNull();
  expect(
    activity
      .find((url) => url.searchParams.get("project_id") === "project-one")
      ?.searchParams.get("include_active"),
  ).toBe("true");

  // More applies only to inactive history; repeated active_rows must not duplicate.
  fireEvent.click(within(project).getByRole("button", { name: /Show more/ }));
  await within(project).findByRole("link", { name: /Older one/ });
  expect(runningLinks()).toHaveLength(6);
  expect(within(project).getAllByRole("link")).toHaveLength(13);

  // Live presentation changes do not trigger a frontend re-sort or touch request.
  activeThreads[1] = { ...activeThreads[1], title: "Active 1 progress" };
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["threads"] });
  });
  await waitFor(() =>
    expect(runningLinks().map((link) => link.textContent)).toEqual(
      activeThreads.map((item) => expect.stringContaining(item.title)),
    ),
  );
  activeThreads = activeThreads.slice(1);
  recentTitle = "Active 0";
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["threads"] });
  });
  await waitFor(() => expect(runningLinks()).toHaveLength(5));
  expect(
    within(project).getAllByRole("link", { name: /Active 0/ }),
  ).toHaveLength(1);
  expect(within(project).queryByText(/outside this page/)).toBeNull();
  expect(within(project).getByRole("link", { name: /Active 0/ })).toBe(
    selectedRow,
  );
  expect(writes).toEqual([]);
});

it("keeps the selected lifecycle observation across pagination but accepts a refreshed first page", async () => {
  activeThreads = [
    { ...thread("Selected"), root_activity: { state: "running" } },
  ];
  mount("/threads/Selected");
  const project = await screen.findByRole("region", { name: "One" });
  await within(project).findByText("Running · 1");
  const row = within(project).getByRole("link", { name: /Selected/ });

  await act(async () => {
    queryClient.setQueryData(["thread", "Selected", "detail"], {
      thread: thread("Selected"),
    });
  });
  await waitFor(() => expect(within(row).queryByText("Running")).toBeNull());
  const more = within(project).getByRole("button", { name: /Show more/ });
  expect(more.textContent).toBe("More");
  fireEvent.click(more);
  await within(project).findByRole("link", { name: /Older one/ });
  expect(within(row).queryByText("Running")).toBeNull();
  expect(within(project).getByRole("link", { name: /Selected/ })).toBe(row);

  // A real first-page refresh is a newer observation, unlike an appended page.
  await act(() => queryClient.invalidateQueries({ queryKey: ["threads"] }));
  await within(row).findByText("Running");
  expect(within(project).getByRole("link", { name: /Selected/ })).toBe(row);
});

it("pins off-page unread results, counts collapsed groups, and keeps running dots independent of archive and pagination", async () => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  const results = new ResultTracker(createTransport("test", () => {}));
  vi.spyOn(results, "invalidate").mockImplementation(() => {});
  const completion = {
    version: 1,
    run_id: "run-done",
    continuation_id: "a".repeat(64),
    completed_at: "2026-09-16T00:00:00Z",
  };
  const unread = {
    ...thread("Off-page result"),
    completion,
  } as Schema<"ThreadSummary">;
  const running = {
    ...thread("Running result"),
    root_activity: { state: "running", run_id: "run-next" },
    completion,
  } as Schema<"ThreadSummary">;
  const archived = {
    ...thread("Archived result"),
    archived: true,
    completion,
  } as Schema<"ThreadSummary">;
  for (const item of [unread, running, archived]) {
    await results.follow({ ...item, completion: null });
    results.observe(item);
  }
  mount("/", false, results);
  const group = await screen.findByRole("region", { name: "One" });
  expect(
    within(group).getByLabelText("2 conversations with new results"),
  ).toBeTruthy();
  expect(
    within(group).queryByRole("link", { name: /Off-page result/ }),
  ).toBeNull();
  fireEvent.click(
    within(group).getByRole("button", { name: /^One/, expanded: false }),
  );
  await screen.findByText("Recent 5");
  expect(within(group).getByText("New results · 1")).toBeTruthy();
  expect(within(group).getByText("Running · 1")).toBeTruthy();
  expect(
    within(group).getAllByRole("img", { name: "New result" }),
  ).toHaveLength(2);
  expect(within(group).queryByText("Archived result")).toBeNull();
  expect(
    within(group).getAllByRole("link", { name: /Off-page result/ }),
  ).toHaveLength(1);
  expect(activity).toHaveLength(1);
  expect(activity[0].searchParams.get("cursor")).toBeNull();
  await act(async () => {
    await results.acknowledge(unread.thread_id, 1);
  });
  expect(
    within(group).queryByRole("link", { name: /Off-page result/ }),
  ).toBeNull();
  expect(
    within(group).getByLabelText("1 conversations with new results"),
  ).toBeTruthy();
  vi.restoreAllMocks();
});

it("surfaces unvisited drafts above collapsed projects and refreshes only discovery on a draft hint", async () => {
  unsentThreads = [
    thread("old-draft"),
    { ...thread("archived-draft"), archived: true },
  ];
  unsentDrafts = unsentThreads.map((item) => ({
    thread_id: item.thread_id,
    draft_id: `draft-${item.thread_id}`,
    unsent_since: "2026-09-21T10:00:00Z",
  }));
  mount("/", true);
  const section = await screen.findByRole("region", { name: "Unsent input" });
  expect(within(section).getByText("Unsent (1)")).toBeTruthy();
  const shortcut = within(section).getByRole("link", { name: /old-draft/ });
  expect(
    within(shortcut).getByRole("img", { name: "Unsent input" }),
  ).toBeTruthy();
  expect(within(shortcut).getByText("One")).toBeTruthy();
  expect(screen.queryByText("archived-draft")).toBeNull();
  expect(activity).toHaveLength(0);
  fireEvent.click(shortcut);
  await waitFor(() =>
    expect(screen.getByLabelText("Current route").textContent).toBe(
      "/threads/old-draft",
    ),
  );
  expect(within(section).getByText("old-draft")).toBeTruthy();
  await screen.findByText("Recent 5");
  const activityCalls = activity.length;
  unsentDrafts = [];
  await act(async () => {
    vi.mocked(watchSummary).mock.calls.at(-1)![1]({
      kind: "draft",
      thread_id: "old-draft",
      epoch: "test",
      sequence: 1,
    });
  });
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Unsent input" })).toBeNull(),
  );
  expect(activity).toHaveLength(activityCalls);
});

it("shows local input immediately, retains the shortcut on opening, and removes it on clear", async () => {
  const draft = new ThreadDraft();
  unsentThreads = [thread("old-draft")];
  mount("/", false, null, draft);
  await screen.findByRole("button", { name: "One" });
  expect(screen.queryByRole("region", { name: "Unsent input" })).toBeNull();
  act(() => {
    draft.doc.getText("text").insert(0, "Remember me");
  });
  const section = await screen.findByRole("region", { name: "Unsent input" });
  await within(section).findByText("old-draft");
  act(() => {
    draft.doc.getText("text").delete(0, draft.doc.getText("text").length);
  });
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Unsent input" })).toBeNull(),
  );
});

it("makes discovery failure retryable rather than silently treating it as an empty index", async () => {
  failDrafts = true;
  mount();
  await screen.findByText("Draft discovery unavailable");
  failDrafts = false;
  unsentThreads = [thread("old-draft")];
  unsentDrafts = [
    {
      thread_id: "old-draft",
      draft_id: "draft-one",
      unsent_since: "2026-09-21T10:00:00Z",
    },
  ];
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Unsent (1)");
});
