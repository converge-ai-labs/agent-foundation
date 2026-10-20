// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { webhookKeys, type Delivery, type Subscription } from "./api";
import { WebhookDeliveries } from "./deliveries";
import { WebhookDetail } from "./detail";
import {
  canRedeliver,
  rebaseDraft,
  subscriptionBody,
  subscriptionDraft,
  subscriptionPatch,
} from "./draft";
import { WebhookEditor } from "./editor";
import { Webhooks } from "./list";

const context = vi.hoisted(() => ({
  client: undefined as unknown as Client,
  workspaceId: "ws_test",
  admin: true,
}));
vi.mock("../../../auth/context", () => ({ useClient: () => context.client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: context.workspaceId },
    basePath: `/workspace/${context.workspaceId}`,
    can: () => context.admin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const subscription: Subscription = {
  id: "sub_test",
  name: "Run outcomes",
  url: "https://example.com/hook",
  kinds: ["run.completed"],
  filter: {},
  enabled: true,
  version: 1,
  workspace_id: "ws_test",
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-10-20T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
};
const delivery: Delivery = {
  id: "obx_test",
  subscription_id: subscription.id,
  url: "https://old.example.com/hook",
  status: "dead",
  attempts: 12,
  last_error: "http_503",
  available_at: "2026-10-20T00:00:00Z",
  created_at: "2026-10-20T00:00:00Z",
  delivered_at: null,
  payload: { type: "run.completed", id: "obx_test" },
};
const fetcher = vi.fn<(request: Request) => Promise<Response>>();
let cache: QueryClient;
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const page = (items: unknown[], next_cursor: string | null = null) =>
  json({ items, next_cursor });
function mount(children: ReactNode, path = "/") {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}
const mutations = (method: string) =>
  fetcher.mock.calls
    .map(([request]) => request)
    .filter((request) => request.method === method);
async function fillCreation(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Run outcomes",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Endpoint URL" }),
    subscription.url,
  );
}
beforeEach(() => {
  context.workspaceId = "ws_test";
  context.admin = true;
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

it("omits blank secrets, clears filters explicitly and rebases only changed fields", () => {
  const original = { ...subscription, filter: { agent_id: "ap_old" } };
  const draft = {
    ...subscriptionDraft(original),
    name: " My name ",
    agentId: "",
  };
  expect(subscriptionBody(draft)).not.toHaveProperty("signing_secret");
  expect(subscriptionPatch(draft, original)).toEqual({
    name: "My name",
    filter: {},
  });
  const latest = {
    ...original,
    version: 2,
    url: "https://new.example.com",
    enabled: false,
  };
  const rebased = rebaseDraft(draft, original, latest);
  expect(rebased).toMatchObject({
    name: " My name ",
    agentId: "",
    url: latest.url,
    enabled: false,
  });
  expect(subscriptionPatch(rebased, latest)).toEqual({
    name: "My name",
    filter: {},
  });
});

it.each([
  ["pending", null, false],
  ["delivered", null, false],
  ["dead", "http_503", true],
  ["dead", "signing_secret_unavailable", false],
])(
  "only offers retry for eligible delivery %s / %s",
  (status, error, expected) => {
    expect(
      canRedeliver({
        status: status as string,
        last_error: error as string | null,
      }),
    ).toBe(expected);
  },
);

it("keeps the one-time secret visible across the first list refresh and out of caches", async () => {
  const user = userEvent.setup();
  let created = false;
  fetcher.mockImplementation(async (request) => {
    if (request.method === "POST") {
      created = true;
      return json({ ...subscription, signing_secret: "whsec_once_only" }, 201);
    }
    return page(created ? [subscription] : []);
  });
  mount(<Webhooks />);
  await screen.findByText("Receive run lifecycle notifications");
  await user.click(
    screen.getAllByRole("button", { name: "Add Webhook" }).at(-1)!,
  );
  await fillCreation(user);
  await user.click(screen.getByRole("button", { name: "Create Webhook" }));
  expect(await screen.findByText("Webhook created")).toBeTruthy();
  await waitFor(() =>
    expect(
      cache.getQueryData(webhookKeys("ws_test").subscription(subscription.id)),
    ).toEqual(subscription),
  );
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain("whsec_once_only");
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state),
    ),
  ).not.toContain("whsec_once_only");
  const request = mutations("POST")[0];
  expect(request.headers.get("X-Workspace-ID")).toBe("ws_test");
  expect(request.headers.get("X-CSRF-Token")).toBe("csrf_test");
  expect(await request.clone().json()).not.toHaveProperty("signing_secret");
  await user.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Add Webhook" }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Add Webhook" }));
  expect(screen.queryByText("Webhook created")).toBeNull();
  expect(
    (
      screen.getByRole("textbox", {
        name: "Name",
      }) as HTMLInputElement
    ).value,
  ).toBe("");
});

it("blocks dismissal and duplicate submission until creation settles", async () => {
  const user = userEvent.setup();
  let finish!: (response: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  mount(<WebhookEditor />);
  await user.click(screen.getByRole("button", { name: "Add Webhook" }));
  await fillCreation(user);
  await user.click(screen.getByRole("button", { name: "Create Webhook" }));
  await user.keyboard("{Escape}");
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(mutations("POST")).toHaveLength(1);
  finish(json({ ...subscription, signing_secret: "whsec_pending" }, 201));
  expect(await screen.findByText("Webhook created")).toBeTruthy();
});

it("requires reviewing saved subscriptions after an unconfirmed create, even when that read fails", async () => {
  const user = userEvent.setup();
  let readsFail = true;
  fetcher.mockImplementation(async (request) => {
    if (request.method === "POST") throw new TypeError("Connection lost");
    return readsFail
      ? json({ error: { message: "Unavailable" } }, 503)
      : page([subscription]);
  });
  mount(<WebhookEditor />);
  await user.click(screen.getByRole("button", { name: "Add Webhook" }));
  await fillCreation(user);
  await user.click(screen.getByRole("button", { name: "Create Webhook" }));
  expect(
    await screen.findByText("Creation could not be confirmed"),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", {
      name: "I checked; allow another create attempt",
    }),
  ).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Create Webhook",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  readsFail = false;
  await user.click(
    screen.getByRole("button", { name: "Review existing Webhooks" }),
  );
  expect(
    await screen.findByRole("button", { name: "Open Webhook" }),
  ).toBeTruthy();
  await user.click(
    screen.getByRole("button", {
      name: "I checked; allow another create attempt",
    }),
  );
  expect(mutations("POST")).toHaveLength(1);
  expect(
    (
      screen.getByRole("textbox", {
        name: "Name",
      }) as HTMLInputElement
    ).value,
  ).toBe("Run outcomes");
});

it("retains edits after 412, then saves only those edits under the refreshed version", async () => {
  const user = userEvent.setup();
  const latest = {
    ...subscription,
    version: 2,
    url: "https://new.example.com/hook",
    enabled: false,
  };
  fetcher.mockImplementation(async (request) => {
    if (request.method === "GET") return json(latest);
    if (mutations("PATCH").length === 1)
      return json({ error: { message: "Changed" } }, 412);
    return json({ ...latest, version: 3, name: "My edits" });
  });
  mount(<WebhookEditor subscription={subscription} />);
  await user.click(screen.getByRole("button", { name: "Edit" }));
  const name = screen.getByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "My edits");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("This Webhook changed")).toBeTruthy();
  expect((name as HTMLInputElement).value).toBe("My edits");
  await user.click(
    screen.getByRole("button", {
      name: "Load current version and keep my edits",
    }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("textbox", {
          name: "Endpoint URL",
        }) as HTMLInputElement
      ).value,
    ).toBe(latest.url),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    mutations("PATCH").map((request) => request.headers.get("If-Match")),
  ).toEqual(['"sub_test:1"', '"sub_test:2"']);
  expect(await mutations("PATCH")[1].clone().json()).toEqual({
    name: "My edits",
  });
});

it("blocks an empty event selection and retains the form", async () => {
  const user = userEvent.setup();
  mount(<WebhookEditor />);
  await user.click(screen.getByRole("button", { name: "Add Webhook" }));
  await fillCreation(user);
  await user.click(screen.getByRole("button", { name: "Clear" }));
  await user.click(screen.getByRole("button", { name: "Create Webhook" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Select at least one event.",
  );
  expect(fetcher).not.toHaveBeenCalled();
});

it("does not read subscriptions or deliveries without workspace admin access", () => {
  context.admin = false;
  mount(
    <>
      <Webhooks />
      <WebhookDetail subscriptionId={subscription.id} />
      <WebhookDeliveries subscriptionId={subscription.id} />
    </>,
  );
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Add Webhook" })).toBeNull();
});

it("drops the open draft and reads the new scope when switching workspaces", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    page([
      {
        ...subscription,
        id: request.headers.get("X-Workspace-ID")!,
        name:
          request.headers.get("X-Workspace-ID") === "ws_test"
            ? "First workspace hook"
            : "Second workspace hook",
      },
    ]),
  );
  const Scoped = () => <Webhooks key={context.workspaceId} />;
  const view = mount(<Scoped />);
  await screen.findByText("First workspace hook");
  await user.click(screen.getByRole("button", { name: "Add Webhook" }));
  await fillCreation(user);
  context.workspaceId = "ws_other";
  view.rerender(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Scoped />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Second workspace hook")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("First workspace hook")).toBeNull();
  expect(mutations("GET").at(-1)!.headers.get("X-Workspace-ID")).toBe(
    "ws_other",
  );
  expect(mutations("POST")).toHaveLength(0);
});

it("pages delivery reads with the server cursor and preserves distinct status labels", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    new URL(request.url).searchParams.has("cursor")
      ? page([{ ...delivery, id: "obx_second", status: "delivered" }])
      : page(
          [delivery, { ...delivery, id: "obx_pending", status: "pending" }],
          "next-page",
        ),
  );
  mount(<WebhookDeliveries subscriptionId={subscription.id} />);
  expect(await screen.findByText("Retries stopped")).toBeTruthy();
  expect(screen.getByText("Pending delivery")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Delivered")).toBeTruthy();
  expect(new URL(mutations("GET").at(-1)!.url).searchParams.get("cursor")).toBe(
    "next-page",
  );
  await user.click(screen.getByRole("button", { name: "Previous" }));
  expect(await screen.findByText("Retries stopped")).toBeTruthy();
});

it("retries with the saved delivery identity and reports queued, not delivered", async () => {
  const user = userEvent.setup();
  let current = delivery;
  fetcher.mockImplementation(async (request) => {
    if (request.method === "POST")
      current = { ...delivery, status: "pending", attempts: 0 };
    return request.method === "POST" ? json(current) : page([current]);
  });
  mount(<WebhookDeliveries subscriptionId={subscription.id} />);
  await user.click(
    await screen.findByRole("button", { name: "run.completed" }),
  );
  expect(screen.getByText(delivery.url)).toBeTruthy();
  expect(screen.getByText("http_503")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Retry delivery" }));
  const confirm = screen.getByRole("dialog", { name: "Retry delivery" });
  await user.click(
    within(confirm).getByRole("button", { name: "Retry delivery" }),
  );
  expect(
    await screen.findByText(
      "Delivery queued again. Refresh to check its result.",
    ),
  ).toBeTruthy();
  expect(mutations("POST")[0].url).toBe(
    "https://service.test/api/v1/subscriptions/sub_test/deliveries/obx_test/redeliver",
  );
  expect(mutations("POST")[0].headers.get("X-Workspace-ID")).toBe("ws_test");
  expect(screen.queryByRole("button", { name: "Retry delivery" })).toBeNull();
  expect(screen.getAllByText("Pending delivery").length).toBeGreaterThan(0);
});

it("refreshes after a retry conflict without showing success", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) =>
    request.method === "POST"
      ? json({ error: { message: "Already being delivered" } }, 409)
      : page([delivery]),
  );
  mount(<WebhookDeliveries subscriptionId={subscription.id} />);
  await user.click(
    await screen.findByRole("button", { name: "run.completed" }),
  );
  await user.click(screen.getByRole("button", { name: "Retry delivery" }));
  await user.click(
    within(screen.getByRole("dialog", { name: "Retry delivery" })).getByRole(
      "button",
      { name: "Retry delivery" },
    ),
  );
  await waitFor(() => expect(mutations("GET").length).toBeGreaterThan(1));
  expect(
    screen.queryByText("Delivery queued again. Refresh to check its result."),
  ).toBeNull();
  expect(screen.getByRole("dialog", { name: "Retry delivery" })).toBeTruthy();
});

it("uses the subscription version for disable and delete", async () => {
  const user = userEvent.setup();
  fetcher.mockImplementation(async (request) => {
    if (request.method === "PATCH")
      return json({ ...subscription, version: 2, enabled: false });
    if (request.method === "DELETE") return new Response(null, { status: 204 });
    return json(subscription);
  });
  mount(
    <WebhookDetail subscriptionId={subscription.id} />,
    "/?tab=configuration",
  );
  await user.click(await screen.findByRole("button", { name: "Disable" }));
  await screen.findByRole("button", { name: "Enable" });
  expect(mutations("PATCH")[0].headers.get("If-Match")).toBe('"sub_test:1"');
  expect(await mutations("PATCH")[0].clone().json()).toEqual({
    enabled: false,
  });
  await user.click(screen.getByRole("button", { name: "Delete" }));
  await user.click(
    within(screen.getByRole("dialog", { name: "Delete Webhook" })).getByRole(
      "button",
      { name: "Delete Webhook" },
    ),
  );
  await waitFor(() => expect(mutations("DELETE")).toHaveLength(1));
  expect(mutations("DELETE")[0].headers.get("If-Match")).toBe('"sub_test:2"');
});
