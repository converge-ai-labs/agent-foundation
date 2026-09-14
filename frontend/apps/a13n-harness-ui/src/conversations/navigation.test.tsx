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
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { ConversationNavigation } from "./navigation";
import { useLiveWorkbench } from "../shell/presence";
import { watchSummary } from "../transport/events";

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
let activity: URL[];
let writes: Request[];
let failMore: boolean;
let failSave: boolean;
let pauseMore: Promise<void> | null;
let pageAborted: boolean;
let recentTitle: string;
let queryClient: QueryClient;
beforeEach(() => {
  localStorage.clear();
  activity = [];
  writes = [];
  failMore = false;
  failSave = false;
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
      if (url.pathname === "/api/configuration/sources")
        return json({ sources: [] });
      if (url.pathname === "/api/selectors")
        return json({ agents: [], environments: [] });
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
function mount(path = "/", live = false) {
  return render(
    <QueryClientProvider client={queryClient}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter initialEntries={[path]}>
          {live ? <LiveNavigation /> : <ConversationNavigation />}
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
    screen.getByText("Selected conversation · outside this page"),
  ).toBeTruthy();
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
  fireEvent.click(screen.getByRole("checkbox", { name: "Include archived" }));
  await waitFor(() =>
    expect(
      activity.filter(
        (url) => url.searchParams.get("include_archived") === "true",
      ),
    ).toHaveLength(4),
  );
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

it("keeps project actions separate from collapse and previews the group's conversation defaults", async () => {
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "New conversation in Two" }),
  );
  await screen.findByRole("dialog", { name: "New conversation" });
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(await writes[0].json()).toMatchObject({ project_id: "project-two" });
  expect(writes[0].url).toContain("/api/threads/configuration-preview");
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
