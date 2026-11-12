import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { credentialKeys } from "../credentials/api";
import { CapturedSessionPolicy } from "../credentials/captured-policy";
import { fixtureThread } from "./transcript/fixture";
import { ThreadSessionBinding } from "./session-binding";
import { conversationKeys, invalidateConversation } from "./api";

const context = vi.hoisted(() => ({ client: undefined as unknown as Client }));
vi.mock("../../auth/context", () => ({ useClient: () => context.client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String(values?.[name] ?? name),
      ),
  }),
}));
const configuration: Schema["SessionConfiguration"] = {
  working_directory: "/original",
  egress_mode: "controlled",
  egress: {
    destinations: { mode: "public" },
    secrets: [
      {
        env: "GH_TOKEN",
        credential_id: "ecred_test",
        inject_hosts: ["api.github.com"],
      },
    ],
  },
};
const mount: Schema["MountView"] = {
  environment_id: "env_test",
  name: "workspace",
  session_binding: "default",
  working_directory: null,
  desktop_enabled: false,
};
const original = {
  name: "default",
  environment_id: "env_test",
  version: 2,
  configuration,
};
const key = [
  ...conversationKeys("ws_test").root,
  "session-binding",
  "thr_1",
  "env_test",
  "default",
];
const fetcher = vi.fn<(request: Request) => Promise<Response>>();
const json = (value: unknown, status = 200, etag?: string) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...(etag ? { ETag: etag } : {}),
    },
  });
let cache: QueryClient;
let current = original;
let etag: string | undefined;
let writeStatus: number;
let credentialVersion: number;
beforeEach(() => {
  current = original;
  etag = '"thr_1:7"';
  writeStatus = 200;
  credentialVersion = 9;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  context.client = createClient({
    baseUrl: "https://service.test",
    auth: { type: "session", csrfToken: "fixture" },
    maxReadRetries: 0,
    fetch: fetcher as typeof fetch,
  });
  fetcher.mockReset();
  fetcher.mockImplementation(async (request) => {
    if (request.method === "PUT")
      return writeStatus === 200
        ? json(current)
        : json(
            {
              error: {
                code:
                  writeStatus === 412
                    ? "precondition_failed"
                    : "internal_error",
                message: "Save could not be confirmed",
                details: {},
              },
            },
            writeStatus,
          );
    if (new URL(request.url).pathname.endsWith("/egress-credentials"))
      return json({
        items: [
          {
            id: "ecred_test",
            name: "GitHub",
            state: "active",
            credential_version: credentialVersion,
            allowed_hosts: ["api.github.com", "github.com"],
          },
        ],
        next_cursor: null,
      });
    return json(current, 200, etag);
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  context.client.close();
});
function show(editable = true) {
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ThreadSessionBinding
          thread={fixtureThread({ version: 99 })}
          mount={mount}
          editable={editable}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
const writes = () =>
  fetcher.mock.calls
    .map(([request]) => request)
    .filter((request) => request.method === "PUT");

it.each([412, 503])(
  "keeps a reviewed Thread ETag and draft through refresh and a %i response",
  async (status) => {
    const user = show();
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Session configuration" }),
    );
    const variable = await screen.findByRole("textbox", {
      name: "Variable name",
    });
    await user.clear(variable);
    await user.type(variable, "CHANGED_TOKEN");
    current = {
      ...original,
      version: 3,
      configuration: {
        ...configuration,
        working_directory: "/updated-by-someone-else",
      },
    };
    etag = '"thr_1:8"';
    await act(async () => {
      await invalidateConversation(cache, "ws_test", { threadId: "thr_1" });
    });
    expect(cache.getQueryData(key)).toMatchObject({ etag: '"thr_1:8"' });
    writeStatus = status;
    await user.click(
      screen.getByRole("button", { name: "Save Session configuration" }),
    );
    await screen.findByText("Review the current Session configuration");
    expect(writes()).toHaveLength(1);
    expect(writes()[0].headers.get("If-Match")).toBe('"thr_1:7"');
    expect(await writes()[0].clone().json()).toMatchObject({
      configuration: {
        working_directory: "/original",
        egress: { secrets: [{ env: "CHANGED_TOKEN" }] },
      },
    });
    expect(
      (
        screen.getByRole("textbox", {
          name: "Variable name",
        }) as HTMLInputElement
      ).value,
    ).toBe("CHANGED_TOKEN");
    await user.click(
      screen.getByRole("button", { name: "Save Session configuration" }),
    );
    expect(writes()).toHaveLength(1);
    etag = '"thr_1:9"';
    writeStatus = 200;
    await user.click(
      screen.getByRole("button", { name: "Load current version" }),
    );
    await screen.findByText(
      "Your draft is unchanged. Saving replaces the current configuration shown below.",
    );
    expect(screen.getByText(/updated-by-someone-else/)).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Save Session configuration" }),
    );
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1].headers.get("If-Match")).toBe('"thr_1:9"');
    expect(await writes()[1].clone().json()).toMatchObject({
      configuration: {
        working_directory: "/original",
        egress: {
          secrets: [
            {
              env: "CHANGED_TOKEN",
              credential_id: "ecred_test",
              inject_hosts: ["api.github.com"],
            },
          ],
        },
      },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  },
);

it("opens read-only configuration without save actions and refuses a read without a Thread ETag", async () => {
  const user = show(false);
  await user.click(
    screen.getByRole("button", { name: "Session configuration" }),
  );
  await screen.findByRole("group", { name: "Variable name" });
  expect(
    screen.queryByRole("button", { name: "Save Session configuration" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Add credential binding" }),
  ).toBeNull();
  await user.click(
    within(screen.getByRole("dialog")).getAllByRole("button", {
      name: "Close",
    })[0],
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.removeQueries({ queryKey: key });
  etag = undefined;
  await user.click(
    screen.getByRole("button", { name: "Session configuration" }),
  );
  await screen.findByText("The Session binding response has no Thread ETag.");
  expect(screen.queryByRole("textbox", { name: "Variable name" })).toBeNull();
  expect(writes()).toHaveLength(0);
});

it("keeps Run references frozen while independently displaying a newer credential version", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <CapturedSessionPolicy
          mount={{
            ...mount,
            session_version: 4,
            session_configuration: configuration,
          }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(
    screen.getByRole("button", { name: "Captured Session configuration" }),
  );
  await screen.findByText("Current credential version 9");
  expect(screen.getByText("Binding default · Session version 4")).toBeTruthy();
  credentialVersion = 10;
  await act(async () => {
    await cache.invalidateQueries({
      queryKey: credentialKeys("ws_test").directory,
    });
  });
  await screen.findByText("Current credential version 10");
  expect(
    within(screen.getByRole("group", { name: "Variable name" })).getByText(
      "GH_TOKEN",
    ),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("checkbox", { name: "api.github.com" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen
      .getByRole("checkbox", { name: "github.com" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(screen.queryByRole("button", { name: "Remove binding" })).toBeNull();
  expect(
    fetcher.mock.calls.every(([request]) => request.method === "GET"),
  ).toBe(true);
});
