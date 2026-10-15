import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunFeedback } from "./pending-request";
import { fixtureRun } from "./fixture";

const { get, post, patch, can } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  can: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({
    http: { GET: get },
    workspace: () => ({ GET: get, POST: post, PATCH: patch }),
  }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/test",
    workspace: { id: "workspace" },
    can,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const ok = (data: unknown, etag?: string) => ({
  data,
  response: new Response(null, { headers: etag ? { etag } : {} }),
});
const definition = {
  type: "openai",
  display_name: "OpenAI",
  configuration_schema: { type: "object", properties: {} },
  authentication: { mode: "required" },
  credential_schema: {
    type: "object",
    required: ["api_key"],
    properties: { api_key: { type: "string", title: "API key", minLength: 1 } },
  },
};
beforeEach(() => {
  can.mockReturnValue(true);
  get.mockImplementation((path: string) =>
    Promise.resolve(
      path.includes("provider-types")
        ? ok({ items: [definition] })
        : ok(
            { id: "provider_1", type: "openai", credential_configured: true },
            '"provider_1:9"',
          ),
    ),
  );
  post.mockImplementation((path: string) =>
    Promise.resolve(
      ok(
        path.includes("resume")
          ? fixtureRun({ id: "next" })
          : { id: "provider_1" },
      ),
    ),
  );
  patch.mockResolvedValue(ok({ id: "provider_1" }));
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function setup(update = false, mixed = false, extra = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RunFeedback
        run={fixtureRun({ status: "waiting" })}
        accepted={() => {}}
        pending={{
          approvals: mixed
            ? [
                {
                  tool_call_id: "approve",
                  tool_name: "other",
                  arguments: {},
                  presentation: null,
                },
              ]
            : [],
          calls: [
            {
              tool_call_id: "provider",
              tool_name: "configure_provider",
              presentation: null,
              arguments: {
                action: update ? "update" : "create",
                kind: "model",
                type: "openai",
                name: "My provider",
                config: {},
                ...(update
                  ? { provider_id: "provider_1", etag: '"provider_1:2"' }
                  : {}),
                ...extra,
              },
            },
          ],
        }}
      />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), queryClient };
}
async function enterSecret(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    await screen.findByLabelText("API key"),
    "fixture-private-key",
  );
  await user.click(screen.getByRole("button", { name: "Save provider" }));
}
const submit = () => screen.getByRole("button", { name: "Submit responses" });

it("sends credentials only to provider storage and resumes the complete mixed batch with a safe result", async () => {
  const { user, queryClient } = setup(false, true);
  await enterSecret(user);
  await screen.findByText(/Provider saved\./);
  expect(screen.queryByLabelText("API key")).toBeNull();
  expect(submit()).toHaveProperty("disabled", true);
  expect(post.mock.calls[0]![0]).toBe("/api/v1/model-providers");
  expect(post.mock.calls[0]![1].body.credential).toEqual({
    api_key: "fixture-private-key",
  });
  await user.click(screen.getByRole("button", { name: "Approve once" }));
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]![1].body).toEqual({
    approvals: { approve: { action: "approve" } },
    calls: {
      provider: {
        status: "returned",
        value: { status: "saved", provider_id: "provider_1" },
      },
    },
  });
  expect(
    JSON.stringify(
      queryClient
        .getMutationCache()
        .getAll()
        .map((m) => m.state),
    ),
  ).not.toContain("fixture-private-key");
});

it("retries only resume after a successful save", async () => {
  const { user } = setup();
  await enterSecret(user);
  await screen.findByText(/Provider saved\./);
  post.mockResolvedValue({
    error: { error: { code: "conflict", message: "Wait changed" } },
    response: new Response(null, { status: 409 }),
  });
  await user.click(submit());
  await screen.findByRole("alert");
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(post.mock.calls[2]).toEqual(post.mock.calls[1]);
  expect(
    post.mock.calls.filter(([path]) => path === "/api/v1/model-providers"),
  ).toHaveLength(1);
});

it("does not retry an uncertain create or expose its raw error and allows cancellation", async () => {
  const { user } = setup();
  post.mockRejectedValue(new Error("fixture-private-key"));
  await enterSecret(user);
  expect((await screen.findByRole("alert")).textContent).not.toContain(
    "fixture-private-key",
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(submit()).toHaveProperty("disabled", true);
  await user.click(
    screen.getByRole("button", { name: "Cancel configuration" }),
  );
  post.mockResolvedValue(ok(fixtureRun()));
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]![1].body.calls.provider.value).toEqual({
    status: "unknown",
  });
});

it("updates with the original proposed ETag and retains an untouched saved credential", async () => {
  const { user } = setup(true);
  await screen.findByLabelText("API key");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Save provider" }),
    ).toHaveProperty("disabled", false),
  );
  await user.click(screen.getByRole("button", { name: "Save provider" }));
  await screen.findByText(/Provider saved\./);
  expect(patch.mock.calls[0]![1].headers).toEqual({
    "If-Match": '"provider_1:2"',
  });
  expect(patch.mock.calls[0]![1].body).toEqual({
    name: "My provider",
    config: {},
  });
  expect(post).not.toHaveBeenCalled();
});

it("requires write access for saving but allows cancelling without a write", async () => {
  can.mockImplementation((permission: string) => permission !== "write");
  const { user } = setup();
  await screen.findByLabelText("API key");
  await user.click(screen.getByRole("button", { name: "Save provider" }));
  expect(post).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Cancel configuration" }),
  );
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![0]).toContain("resume");
  expect(post.mock.calls[0]![1].body.calls.provider.value).toEqual({
    status: "cancelled",
  });
});

it("validates required credentials before any save and can cancel cleanly", async () => {
  const { user } = setup();
  await screen.findByLabelText("API key");
  await user.click(screen.getByRole("button", { name: "Save provider" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Check the required credential fields",
  );
  expect(post).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Cancel configuration" }),
  );
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.calls.provider.value).toEqual({
    status: "cancelled",
  });
});

it("saves a provider without credentials when its definition forbids authentication", async () => {
  get.mockResolvedValue(
    ok({
      items: [
        {
          ...definition,
          type: "duckduckgo",
          authentication: { mode: "forbidden" },
          credential_schema: null,
        },
      ],
    }),
  );
  const { user } = setup(false, false, { kind: "web", type: "duckduckgo" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Save provider" }).closest("fieldset"),
    ).toHaveProperty("disabled", false),
  );
  expect(screen.queryByLabelText("API key")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save provider" }));
  await screen.findByText(/Provider saved\./);
  expect(post.mock.calls[0]![0]).toBe("/api/v1/web-providers");
  expect(post.mock.calls[0]![1].body).toEqual({
    type: "duckduckgo",
    name: "My provider",
    config: {},
  });
});

it.each([
  { action: ["create"] },
  { credential: { api_key: "untrusted" } },
  { kind: "memory" },
  { action: "update", provider_id: "provider_1" },
])(
  "refuses unsupported proposals instead of a generic JSON response: %j",
  async (extra) => {
    const { user } = setup(false, false, extra);
    expect(screen.getByText(/This provider request is invalid/)).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Response" })).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Cancel configuration" }),
    );
    expect(post).not.toHaveBeenCalled();
  },
);
