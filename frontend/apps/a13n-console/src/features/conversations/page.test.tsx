// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { ConversationsPage, NewConversation } from "./page";
import { fixtureRun, fixtureThread } from "./transcript/fixture";

let client: Client;
let allowed: string[] = [];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/ws_design",
    organization: { id: "organization" },
    workspace: { id: "workspace" },
    can: (action: string) => allowed.includes(action),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  client.close();
  allowed = [];
});

it("renders a full Session page from collection previews without per-row Thread or Run requests", async () => {
  const requests: string[] = [];
  const urls: URL[] = [];
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(new URL(request.url).pathname);
      urls.push(new URL(request.url));
      return Response.json({
        items: Array.from({ length: 20 }, (_, index) => ({
          id: `session_${index}`,
          workspace_id: "workspace",
          labels: {},
          title: `Question ${index}`,
          created_at: "2026-09-09T00:00:00Z",
          updated_at: "2026-09-09T00:00:00Z",
          run_count: index === 19 ? null : index + 1,
          preview:
            index === 19
              ? null
              : {
                  thread_id: `thread_${index}`,
                  run_id: `run_${index}`,
                  agent_id: `agt_${index}`,
                  agent_name: `Agent ${index}`,
                  input_text: `Question ${index}`,
                  output_text: `**Answer ${index}**`,
                  status: "completed",
                  trigger: "input",
                },
        })),
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/ws_design/sessions"]}>
        <Routes>
          <Route
            path="/workspace/:workspaceId/sessions"
            element={<ConversationsPage />}
          >
            <Route path=":sessionId/*" element={<SelectedDetail />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Question 18")).toBeTruthy();
  expect(screen.getByText("Agent 18")).toBeTruthy();
  expect(
    screen.getAllByRole("columnheader").map((column) => column.textContent),
  ).toEqual([
    "Session",
    "Source",
    "Status",
    "Runs",
    "Updated",
    "Session actions",
  ]);
  expect(screen.getByText("Not started")).toBeTruthy();
  expect(screen.getByRole("searchbox")).toBeTruthy();
  expect(screen.queryByText("Session detail")).toBeNull();
  expect(requests).toEqual(["/api/v1/sessions"]);
  const row = screen.getByText("Question 0").closest("tr")!;
  await user.click(
    within(row).getByRole("button", { name: "Session actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Copy session ID" }),
  );
  expect(copy).toHaveBeenCalledWith("session_0");
  expect(screen.queryByText("Session detail")).toBeNull();
  await user.click(within(row).getByText("Question 0"));
  expect(
    await screen.findByText(
      "/workspace/ws_design/sessions/session_0?view=debug",
    ),
  ).toBeTruthy();
  cleanup();
  cache.clear();
});

function SelectedDetail() {
  const { pathname, search } = useLocation();
  return <p>{`${pathname}${search}`}</p>;
}

it("updates ID search, combines filters, and opens the matched Thread", async () => {
  const urls: URL[] = [];
  const user = userEvent.setup();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      urls.push(url);
      return Response.json({
        items: [
          {
            id: "sess_one",
            labels: {},
            workspace_id: "workspace",
            created_at: "2026-09-09T00:00:00Z",
            updated_at: "2026-09-09T00:00:00Z",
            preview: null,
            run_count: 0,
          },
        ],
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={["/workspace/ws_design/sessions?agent_id=agt_one"]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceId/sessions"
            element={<ConversationsPage />}
          >
            <Route path=":sessionId/*" element={<SelectedDetail />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Untitled session");
  expect(urls[0].searchParams.get("agent_id")).toBe("agt_one");
  await user.type(
    screen.getByRole("searchbox"),
    "  thread_0123456789abcdef0123456789abcdef  ",
  );
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.get("q")).toBe(
      "thread_0123456789abcdef0123456789abcdef",
    ),
  );
  await user.click(screen.getByRole("button", { name: "Status" }));
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "state.failed" }),
  );
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "state.waiting" }),
  );
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.getAll("status")).toEqual([
      "failed",
      "waiting",
    ]),
  );
  await user.click(screen.getByRole("button", { name: "Trigger" }));
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "trigger.queued" }),
  );
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.getAll("trigger")).toEqual(["queued"]),
  );
  await user.click(screen.getByRole("button", { name: "Updated" }));
  expect(
    await screen.findByRole("button", { name: "Start time" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "End time" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Last 7 days" }));
  expect(urls.at(-1)?.searchParams.has("updated_after")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.has("updated_before")).toBe(true),
  );
  const bounds = urls.at(-1)!.searchParams;
  expect(
    Date.parse(bounds.get("updated_before")!) -
      Date.parse(bounds.get("updated_after")!),
  ).toBe(7 * 86400000);
  expect(urls.at(-1)?.searchParams.get("agent_id")).toBe("agt_one");
  await user.click(screen.getByText("Untitled session").closest("tr")!);
  expect(
    await screen.findByText(
      "/workspace/ws_design/sessions/sess_one/threads/thread_0123456789abcdef0123456789abcdef?view=debug",
    ),
  ).toBeTruthy();
  cleanup();
  cache.clear();
});

it.each(["inherit", "off", "on", "spaces"])(
  "starts a Console session with memory %s, reusing preparation on retry",
  async (memoryChoice) => {
    allowed = ["run"];
    const posts: { path: string; body: unknown; key: string | null }[] = [];
    let failures = 1;
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session", csrfToken: "csrf" },
      fetch: async (input, init) => {
        const request = new Request(input, init);
        const path = new URL(request.url).pathname;
        if (request.method === "POST") {
          const body: unknown = await request.json();
          posts.push({
            path,
            body,
            key: request.headers.get("Idempotency-Key"),
          });
          if (path.endsWith("/sessions"))
            return Response.json(
              {
                id: "ses_1",
                workspace_id: "workspace",
                labels: { "a13n.console": "debug" },
                origin: { application: "a13n-console", name: "Console" },
                created_by_id: "usr_1",
                last_run_id: null,
                version: 1,
                created_at: "2026-09-20T10:00:00.000Z",
                updated_at: "2026-09-20T10:00:00.000Z",
              },
              { status: 201 },
            );
          if (path.endsWith("/environments"))
            return Response.json(
              {
                id: "env_new",
                template_id: "envt_research",
                status: "creating",
              },
              { status: 201 },
            );
          if (failures-- > 0)
            return Response.json(
              { error: { code: "unavailable", message: "Try again." } },
              { status: 503 },
            );
          return Response.json(
            {
              thread: fixtureThread(),
              entry: {},
              run: fixtureRun({ id: "run_1", status: "accepted" }),
            },
            { status: 201 },
          );
        }
        if (path.endsWith("/media-understanding-defaults"))
          return Response.json({
            id: "workspace",
            version: 0,
            image: null,
            video: null,
            audio: null,
          });
        return Response.json({
          items: path.endsWith("/agents")
            ? [{ id: "agt_1", name: "Release Bot", image_url: null }]
            : path.endsWith("/environment-templates")
              ? [{ id: "envt_research", name: "Research" }]
              : path.endsWith("/memory-spaces")
                ? [{ id: "msp_team", name: "Team knowledge", enabled: true }]
                : [],
          next_cursor: null,
        });
      },
    });
    const cache = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter initialEntries={["/sessions/new?agent=agt_1"]}>
          <Routes>
            <Route path="/sessions/new" element={<NewConversation />} />
            <Route path="*" element={<SelectedDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Options" }));
    await user.click(screen.getByRole("combobox", { name: "Environment" }));
    await user.click(
      await screen.findByRole("option", {
        name: /^Research.*Create from template$/,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Apply" }));
    if (memoryChoice !== "inherit") {
      await user.click(
        screen.getByRole("button", { name: /Memory.*Follow agent/ }),
      );
      await user.click(screen.getByRole("button", { name: "Custom" }));
      if (memoryChoice === "on" || memoryChoice === "spaces")
        await user.click(screen.getByRole("switch", { name: "Memory spaces" }));
      if (memoryChoice === "spaces") {
        await user.click(screen.getByRole("button", { name: "Add space" }));
        await user.click(
          await screen.findByRole("checkbox", { name: /Team knowledge/ }),
        );
        await user.keyboard("{Escape}");
        await user.clear(screen.getByRole("textbox", { name: "Mount name" }));
        await user.keyboard("{Escape}");
        await user.type(
          screen.getByRole("textbox", { name: "Message" }),
          "Check memory",
        );
        expect(screen.getByRole("button", { name: "Send" })).toHaveProperty(
          "disabled",
          true,
        );
        await user.keyboard("{Enter}");
        expect(posts).toEqual([]);
        await user.clear(screen.getByRole("textbox", { name: "Message" }));
        await user.click(
          screen.getByRole("button", { name: /Memory.*Review settings/ }),
        );
        await user.type(
          screen.getByRole("textbox", { name: "Mount name" }),
          "team",
        );
        await user.click(screen.getByRole("button", { name: "Follow agent" }));
        expect(
          screen.queryByRole("textbox", { name: "Mount name" }),
        ).toBeNull();
        await user.click(screen.getByRole("button", { name: "Custom" }));
        expect(
          screen.getByRole("textbox", { name: "Mount name" }),
        ).toHaveProperty("value", "team");
      }
      await user.click(screen.getByRole("button", { name: "Done" }));
    }
    await user.type(
      screen.getByRole("textbox", { name: "Message" }),
      "Run the checks",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Try again.");
    await user.click(screen.getByRole("button", { name: /^Memory/ }));
    expect(screen.getByRole("button", { name: "Custom" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "Follow agent" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(
      await screen.findByText(
        "/workspace/ws_design/sessions/ses_1/threads/thr_1/runs/run_1?follow=latest",
      ),
    ).toBeTruthy();
    expect(posts.map(({ path }) => path)).toEqual([
      "/api/v1/sessions",
      "/api/v1/environments",
      "/api/v1/threads",
      "/api/v1/threads",
    ]);
    expect(posts[0]?.body).toEqual({
      labels: { "a13n.console": "debug" },
      origin: { application: "a13n-console", name: "Console" },
      ...(memoryChoice === "inherit"
        ? {}
        : {
            memory: {
              enabled: memoryChoice === "on" || memoryChoice === "spaces",
              spaces:
                memoryChoice === "spaces"
                  ? [{ name: "team", space_id: "msp_team", access: "write" }]
                  : [],
            },
          }),
    });
    expect(posts[1]?.body).toEqual({ template_id: "envt_research" });
    expect(posts[2]?.body).toEqual({
      agent_id: "agt_1",
      payload: {
        content: [
          {
            type: "text",
            text: "Run the checks",
          },
        ],
      },
      session_id: "ses_1",
      environments: [{ name: "workspace", environment_id: "env_new" }],
    });
    // The retry replays the same submission.
    expect(posts[3]).toEqual(posts[2]);
    cache.clear();
  },
);

it.each(["writer", "runner", "forbidden", "pinned", "unavailable"])(
  "starts an existing builtin agent from Try agent as %s",
  async (scenario) => {
    allowed =
      scenario === "runner" ? ["read", "run"] : ["read", "run", "write"];
    const posts: string[] = [];
    const submissions: { body: unknown; key: string | null }[] = [];
    let releasePreparation!: () => void;
    const preparation = new Promise<void>((resolve) => {
      releasePreparation = resolve;
    });
    let threadAttempts = 0;
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session", csrfToken: "csrf" },
      fetch: async (input, init) => {
        const request = new Request(input, init);
        const path = new URL(request.url).pathname;
        if (request.method === "POST") {
          posts.push(path);
          if (path.endsWith("/agent-composer")) {
            await preparation;
            if (scenario === "forbidden" || scenario === "unavailable")
              return Response.json(
                { error: { code: scenario, message: "Preparation failed" } },
                { status: scenario === "forbidden" ? 403 : 503 },
              );
            return Response.json({
              id: "agt_builtin",
              default_revision_id: "apr_new",
            });
          }
          if (path.endsWith("/sessions"))
            return Response.json({ id: "ses_1" }, { status: 201 });
          if (path.endsWith("/threads")) {
            submissions.push({
              body: await request.json(),
              key: request.headers.get("Idempotency-Key"),
            });
            if (scenario === "writer" && threadAttempts++ === 0)
              return Response.json(
                {
                  error: {
                    code: "unavailable",
                    message: "Retry the submission",
                  },
                },
                { status: 503 },
              );
            return Response.json(
              {
                thread: fixtureThread(),
                entry: {},
                run: fixtureRun({ id: "run_1" }),
              },
              { status: 201 },
            );
          }
          throw new Error(`Unexpected POST ${path}`);
        }
        if (path.endsWith("/media-understanding-defaults"))
          return Response.json({
            id: "workspace",
            version: 0,
            image: null,
            video: null,
            audio: null,
          });
        return Response.json({
          items: path.endsWith("/agents")
            ? [
                {
                  id: "agt_builtin",
                  name: "Agent Composer",
                  source: "builtin",
                  preset_kind: "composer",
                  default_revision_id: "apr_old",
                  image_url: null,
                },
              ]
            : [],
          next_cursor: null,
        });
      },
    });
    const cache = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter initialEntries={["/sessions/new?agent=agt_builtin"]}>
          <Routes>
            <Route path="/sessions/new" element={<NewConversation />} />
            <Route path="*" element={<SelectedDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await screen.findByText("Agent Composer");
    if (scenario === "pinned") {
      await user.click(screen.getByRole("button", { name: "Options" }));
      await user.click(screen.getByRole("button", { name: "Advanced" }));
      await user.type(
        screen.getByRole("textbox", { name: "Pinned agent revision ID" }),
        "apr_old",
      );
      await user.click(screen.getByRole("button", { name: "Apply" }));
    }
    await user.type(
      screen.getByRole("textbox", { name: "Message" }),
      "Configure a provider",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    const shouldPrepare = scenario !== "runner" && scenario !== "pinned";
    if (shouldPrepare) {
      await waitFor(() => expect(posts).toEqual(["/api/v1/agent-composer"]));
      releasePreparation();
    }
    if (scenario === "unavailable") {
      await screen.findByText("Preparation failed");
      expect(posts).toEqual(["/api/v1/agent-composer"]);
      expect(submissions).toEqual([]);
    } else {
      if (scenario === "writer") {
        await screen.findByText("Retry the submission");
        await user.click(screen.getByRole("button", { name: "Send" }));
      }
      await screen.findByText(
        "/workspace/ws_design/sessions/ses_1/threads/thr_1/runs/run_1?follow=latest",
      );
      expect(posts).toEqual([
        ...(shouldPrepare ? ["/api/v1/agent-composer"] : []),
        "/api/v1/sessions",
        "/api/v1/threads",
        ...(scenario === "writer" ? ["/api/v1/threads"] : []),
      ]);
      expect(submissions[0]!.body).toMatchObject({ agent_id: "agt_builtin" });
      if (scenario === "pinned")
        expect(submissions[0]!.body).toHaveProperty(
          "agent_revision_id",
          "apr_old",
        );
      else expect(submissions[0]!.body).not.toHaveProperty("agent_revision_id");
      if (scenario === "writer") expect(submissions[1]).toEqual(submissions[0]);
    }
    cache.clear();
  },
);

it("prefills the first message from the link, leaving sending to the user", async () => {
  allowed = ["run"];
  const posts: string[] = [];
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      if (request.method === "POST") posts.push(request.url);
      return Response.json({
        items: [{ id: "agt_assistant", name: "Assistant", image_url: null }],
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          "/sessions/new?agent=agt_assistant&message=Help+me+change+the+agent",
        ]}
      >
        <Routes>
          <Route path="/sessions/new" element={<NewConversation />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "Help me change the agent",
  );
  expect(await screen.findByRole("button", { name: "Send" })).toBeTruthy();
  expect(posts).toEqual([]);
  cache.clear();
});
