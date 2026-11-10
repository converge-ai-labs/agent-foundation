// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider } from "a13n-ui";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import { credentialKeys, type Credential, type SessionCredential } from "./api";
import { CredentialDetail } from "./detail";
import { useCredentialEditor } from "./editor";
import { CredentialsPage } from "./page";
import { CredentialSessions } from "./sessions";
import { RevokeCredential } from "./revoke";
import { EnvironmentCredentials } from "./environment";

function CredentialEditor(props: Parameters<typeof useCredentialEditor>[0]) {
  const editor = useCredentialEditor(props);
  return (
    <>
      {editor.button}
      {editor.dialog}
    </>
  );
}

const context = vi.hoisted(() => ({
  client: undefined as unknown as Client,
  write: true,
}));
vi.mock("../../auth/context", () => ({ useClient: () => context.client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) => verb !== "write" || context.write,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const credential: Credential = {
  id: "ecred_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  name: "GitHub automation",
  description: "Repository checks",
  allowed_hosts: ["api.github.com"],
  state: "active",
  credential_version: 1,
  version: 1,
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-11-10T00:00:00Z",
  updated_at: "2026-11-10T00:00:00Z",
  revoked_at: null,
};
const reference: SessionCredential = {
  thread_id: "thread_test",
  environment_id: "env_test",
  environment_name: "Test sandbox",
  binding_name: "default",
  session_version: 1,
  env: "GH_TOKEN",
  credential_id: credential.id,
  credential_name: credential.name,
  credential_state: "active",
  credential_version: 2,
  inject_hosts: credential.allowed_hosts,
  status: "on_next_use",
  execution_active: false,
  native_session_id: "native_test",
  applied_credential_version: 1,
  applied_session_id: "native_test",
  applied_device_generation: "18446744073709551615",
  applied_at: credential.created_at,
};
const fetcher = vi.fn<(request: Request) => Promise<Response>>();
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const page = (items: unknown[], next_cursor: string | null = null) =>
  json({ items, next_cursor });
let cache: QueryClient;
function mount(children: ReactNode, path = "/") {
  return render(
    <QueryClientProvider client={cache}>
      <ToastProvider closeLabel="Dismiss">
        <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}
function detail(path = "/credentials/ecred_test") {
  return mount(
    <Routes>
      <Route path="/credentials/:credentialId" element={<CredentialDetail />} />
    </Routes>,
    path,
  );
}
const requests = (method: string) =>
  fetcher.mock.calls
    .map(([request]) => request)
    .filter((request) => request.method === method);
async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    credential.name,
  );
  await user.type(
    screen.getByRole("textbox", { name: "Domains" }),
    "api.github.com",
  );
  await user.type(
    screen.getByLabelText("Token", { exact: true }),
    "fixture-private-token",
  );
}
beforeEach(() => {
  context.write = true;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  context.client = createClient({
    baseUrl: "https://service.test",
    auth: { type: "session", csrfToken: "csrf_test" },
    maxReadRetries: 0,
    fetch: fetcher as typeof fetch,
  });
  fetcher.mockReset();
});
afterEach(() => {
  cleanup();
  cache.clear();
  context.client.close();
});

it("creates a credential without caching its token and clears input on close and success", async () => {
  const user = userEvent.setup();
  fetcher.mockResolvedValue(json(credential, 201));
  mount(<CredentialEditor operation={{ kind: "create" }} />);
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  expect(screen.getByLabelText("Token", { exact: true })).toHaveProperty(
    "value",
    "",
  );
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create credential" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  expect(requests("POST")).toHaveLength(1);
  expect(requests("POST")[0].headers.get("X-Workspace-ID")).toBe("ws_test");
  expect(await requests("POST")[0].clone().json()).toMatchObject({
    allowed_hosts: ["api.github.com"],
    value: "fixture-private-token",
  });
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((q) => q.state),
    ),
  ).not.toContain("fixture-private-token");
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((m) => m.state),
    ),
  ).not.toContain("fixture-private-token");
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  expect(screen.getByLabelText("Token", { exact: true })).toHaveProperty(
    "value",
    "",
  );
});

it("allows dismissal during save without reopening or navigating after completion", async () => {
  const user = userEvent.setup(),
    onSaved = vi.fn();
  let finish!: (response: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  mount(<CredentialEditor operation={{ kind: "create" }} onSaved={onSaved} />);
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create credential" }));
  await user.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  expect(screen.getByRole("button", { name: "Add credential" })).toHaveProperty(
    "disabled",
    true,
  );
  finish(json(credential, 201));
  await waitFor(() =>
    expect(
      cache.getQueryData(credentialKeys("ws_test").item(credential.id)),
    ).toEqual(credential),
  );
  expect(onSaved).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("dialog", {
      name: /^(Add|Edit|Rotate|Revoke) credential$/,
    }),
  ).toBeNull();
});

it("blocks duplicate creation after an uncertain response and retains input", async () => {
  const user = userEvent.setup();
  fetcher.mockRejectedValue(new TypeError("Connection lost"));
  mount(<CredentialEditor operation={{ kind: "create" }} />);
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create credential" }));
  await screen.findByText("Save could not be confirmed");
  expect(
    screen.getByRole("button", { name: "Create credential" }),
  ).toHaveProperty("disabled", true);
  expect(requests("POST")).toHaveLength(1);
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveProperty(
    "value",
    credential.name,
  );
});

it("keeps rotation's original version through refresh and requires explicit stale recovery", async () => {
  const user = userEvent.setup(),
    latest = { ...credential, version: 2, credential_version: 2 };
  fetcher.mockImplementation(async (request) =>
    request.method === "GET"
      ? json(latest)
      : requests("PUT").length === 1
        ? json(
            { error: { code: "precondition_failed", message: "Changed" } },
            412,
          )
        : json({ ...latest, version: 3 }),
  );
  const view = mount(
    <CredentialEditor operation={{ kind: "rotate", credential }} />,
  );
  await user.click(screen.getByRole("button", { name: "Rotate credential" }));
  await user.type(
    screen.getByLabelText("Token", { exact: true }),
    "replacement-token",
  );
  view.rerender(
    <QueryClientProvider client={cache}>
      <ToastProvider closeLabel="Dismiss">
        <MemoryRouter>
          <CredentialEditor
            operation={{ kind: "rotate", credential: latest }}
          />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Save new token" }));
  await screen.findByText("This credential changed");
  expect(screen.getByLabelText("Token", { exact: true })).toHaveProperty(
    "value",
    "replacement-token",
  );
  await user.click(
    screen.getByRole("button", {
      name: "Load current version and keep my edits",
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Save new token" }),
    ).toHaveProperty("disabled", false),
  );
  await user.click(screen.getByRole("button", { name: "Save new token" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  expect(requests("PUT").map((r) => r.headers.get("If-Match"))).toEqual([
    '"ecred_test:1"',
    '"ecred_test:2"',
  ]);
  expect(await requests("PUT")[1].clone().json()).toEqual({
    value: "replacement-token",
  });
});

it("searches the complete credential collection, including later pages", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    new URL(request.url).searchParams.has("cursor")
      ? page([{ ...credential, id: "ecred_late", name: "Late token" }])
      : page([credential], "page-two"),
  );
  mount(<CredentialsPage />);
  await screen.findByRole("link", { name: /GitHub automation/ });
  await user.type(
    screen.getByRole("searchbox", { name: "Search credentials" }),
    "Late",
  );
  expect(await screen.findByRole("link", { name: /Late token/ })).toBeTruthy();
  await waitFor(() =>
    expect(
      screen.queryByRole("link", { name: /GitHub automation/ }),
    ).toBeNull(),
  );
});

it("shows saved revocation separately from idle removal and retries only through the sync endpoint", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    new URL(request.url).pathname.endsWith("/sessions")
      ? page([{ ...reference, credential_state: "revoked" }])
      : json({ ...credential, state: "revoked", credential_version: 2 }),
  );
  detail();
  expect(await screen.findByText("On next use")).toBeTruthy();
  expect(screen.queryByText("Removal confirmed")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Sync active Sessions" }),
  );
  await screen.findByText(
    "Synchronization requested. Check each Session for confirmation; idle Sessions were not connected.",
  );
  expect(requests("POST")).toHaveLength(1);
  expect(requests("POST")[0].url).toBe(
    "https://service.test/api/v1/egress-credentials/ecred_test/sync",
  );
});

it("keeps metadata and Session status readable without mutation authority", async () => {
  context.write = false;
  fetcher.mockImplementation(async (request) =>
    new URL(request.url).pathname.endsWith("/sessions")
      ? page([reference])
      : json(credential),
  );
  detail();
  await screen.findByText("On next use");
  expect(
    screen.queryByRole("button", { name: "Rotate credential" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit credential" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Sync active Sessions" }),
  ).toBeNull();
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
});

it("pages Session evidence without treating an old applied version as removal", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    new URL(request.url).searchParams.has("cursor")
      ? page([
          {
            ...reference,
            credential_state: "revoked",
            status: "applied",
            applied_credential_version: 2,
          },
        ])
      : page([reference], "page-two"),
  );
  mount(<CredentialSessions target={{ credentialId: credential.id }} />);
  await screen.findByText("On next use");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Removal confirmed");
  expect(new URL(requests("GET").at(-1)!.url).searchParams.get("cursor")).toBe(
    "page-two",
  );
});

it("freezes revocation's reviewed version and reloads before another confirmation", async () => {
  const user = userEvent.setup(),
    latest = { ...credential, version: 2, credential_version: 2 };
  fetcher.mockImplementation(async (request) =>
    request.method === "GET"
      ? json(latest)
      : requests("POST").length === 1
        ? json(
            { error: { code: "precondition_failed", message: "Changed" } },
            412,
          )
        : json({ ...latest, state: "revoked", version: 3 }),
  );
  const view = mount(<RevokeCredential credential={credential} />);
  await user.click(screen.getByRole("button", { name: "Revoke" }));
  expect(
    screen.getByText(
      "Idle Sessions may retain the old token until next use or expiry. This does not revoke the token at its upstream provider.",
    ),
  ).toBeTruthy();
  view.rerender(
    <QueryClientProvider client={cache}>
      <ToastProvider closeLabel="Dismiss">
        <MemoryRouter>
          <RevokeCredential credential={latest} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Revoke credential",
    }),
  );
  await screen.findByText("Review the current credential");
  expect(
    screen.getByRole("button", { name: "Revoke credential" }),
  ).toHaveProperty("disabled", true);
  await user.click(
    screen.getByRole("button", { name: "Load current version" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Revoke credential" }),
    ).toHaveProperty("disabled", false),
  );
  await user.click(screen.getByRole("button", { name: "Revoke credential" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  expect(requests("POST").map((r) => r.headers.get("If-Match"))).toEqual([
    '"ecred_test:1"',
    '"ecred_test:2"',
  ]);
});

it("preserves a creation draft when a background refresh replaces the empty state", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async () => page([]));
  mount(<CredentialsPage />);
  await screen.findByText("No credentials yet");
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  await fill(user);
  act(() =>
    cache.setQueryData(
      [...credentialKeys("ws_test").lists, undefined, "", "all"],
      { items: [credential], next_cursor: null },
    ),
  );
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.getByLabelText("Token", { exact: true })).toHaveProperty(
    "value",
    "fixture-private-token",
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: /^(Add|Edit|Rotate|Revoke) credential$/,
      }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Add credential" }));
  expect(screen.getByLabelText("Token", { exact: true })).toHaveProperty(
    "value",
    "",
  );
});

it("reads environment application evidence only after opening its credential section", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async () => page([reference]));
  mount(<EnvironmentCredentials environmentId="env_test" />);
  expect(requests("GET")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Credentials" }));
  await screen.findByText("On next use");
  expect(requests("GET")).toHaveLength(1);
  expect(requests("GET")[0].url).toBe(
    "https://service.test/api/v1/environments/env_test/credential-sessions?limit=30",
  );
  expect(requests("GET")[0].headers.get("X-Workspace-ID")).toBe("ws_test");
  expect(requests("POST")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Credentials" }));
  await waitFor(() => expect(screen.queryByText("On next use")).toBeNull());
});
