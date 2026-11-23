import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunFeedback } from "./pending-request";
import { fixtureRun } from "./fixture";
import type { ConfigurationTool } from "./private-configuration";
import type { Schema } from "../../../shared/api";

const { get, post, patch, put, can } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  can: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({
    workspace: () => ({ GET: get, POST: post, PATCH: patch, PUT: put }),
  }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/workspace",
    can,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const secret = "fixture-private-token";
const signingSecret = `whsec_${btoa("a".repeat(32))}`;
const credentialId = "ecred_" + "a".repeat(20);
const subscriptionId = "sub_" + "a".repeat(20);
const ok = (data: unknown) => ({ data, response: new Response() });
const saved = {
  id: credentialId,
  version: 4,
  credential_version: 2,
  owner_app_id: null,
  name: "GitHub",
  allowed_hosts: ["api.github.com"],
  state: "active",
};
beforeEach(() => {
  can.mockReturnValue(true);
  get.mockResolvedValue(ok(saved));
  patch.mockResolvedValue(ok(saved));
  put.mockResolvedValue(ok(saved));
  post.mockImplementation((path: string) =>
    Promise.resolve(
      ok(
        path.includes("resume")
          ? fixtureRun()
          : path.includes("subscriptions")
            ? { id: subscriptionId, version: 1, signing_secret: signingSecret }
            : saved,
      ),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(
  request: Schema["JsonValue"],
  tool: ConfigurationTool = "configure_egress_credential",
  mixed = false,
) {
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
                  tool_call_id: "approval",
                  tool_name: "other",
                  arguments: {},
                  presentation: null,
                },
              ]
            : [],
          calls: [
            {
              tool_call_id: "private",
              tool_name: tool,
              arguments: { request },
              presentation: null,
            },
          ],
        }}
      />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), queryClient };
}
const create = {
  action: "create",
  name: "GitHub",
  allowed_hosts: ["api.github.com"],
};
const submit = () => screen.getByRole("button", { name: "Submit responses" });
async function saveToken(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Token"), secret);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
}
function cacheState(cache: QueryClient) {
  return JSON.stringify([
    cache
      .getMutationCache()
      .getAll()
      .map((m) => m.state),
    cache
      .getQueryCache()
      .getAll()
      .map((q) => q.state),
  ]);
}
it("keeps token values out of caches and resumes a mixed batch with only identity and versions", async () => {
  const { user, queryClient } = setup(create, undefined, true);
  await saveToken(user);
  await screen.findByText(/^Saved\. Submit/);
  expect(post.mock.calls[0]![1].body.value).toBe(secret);
  expect(cacheState(queryClient)).not.toContain(secret);
  expect(submit()).toHaveProperty("disabled", true);
  await user.click(screen.getByRole("button", { name: "Approve once" }));
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]![1].body.calls.private.value).toEqual({
    status: "saved",
    credential_id: credentialId,
    version: 4,
    credential_version: 2,
  });
  expect(post.mock.calls[1]![1].body.approvals).toEqual({
    approval: { action: "approve" },
  });
});
it("reveals a generated signing key locally and requires acknowledgment before resume", async () => {
  const { user, queryClient } = setup(
    {
      action: "create",
      name: "Runs",
      url: "https://example.com/hook",
      kinds: ["run.completed"],
    },
    "configure_webhook",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("button", { name: "I saved the secret" });
  expect(submit()).toHaveProperty("disabled", true);
  expect(cacheState(queryClient)).not.toContain(signingSecret);
  expect(post.mock.calls[0]![1].body).not.toHaveProperty("signing_secret");
  await user.click(screen.getByRole("button", { name: "I saved the secret" }));
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]![1].body.calls.private.value).toEqual({
    status: "saved",
    subscription_id: subscriptionId,
    version: 1,
  });
});
it("retains the proposal version for rotation and never repeats a save when resume fails", async () => {
  const { user } = setup({
    action: "rotate",
    credential_id: credentialId,
    expected_version: `${credentialId}:1`,
  });
  await waitFor(() =>
    expect(screen.getByLabelText("Token").closest("fieldset")).toHaveProperty(
      "disabled",
      false,
    ),
  );
  await saveToken(user);
  await screen.findByText(/^Saved\. Submit/);
  expect(put.mock.calls[0]![1].headers).toEqual({
    "If-Match": `"${credentialId}:1"`,
  });
  post.mockResolvedValue({
    error: { error: { code: "conflict", message: "Wait changed" } },
    response: new Response(null, { status: 409 }),
  });
  await user.click(submit());
  await screen.findByRole("alert");
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1]).toEqual(post.mock.calls[0]);
  expect(put).toHaveBeenCalledTimes(1);
});
it.each([412, 500])(
  "blocks a second write after HTTP %i and returns unknown without exposing server text",
  async (status) => {
    const { user, queryClient } = setup(create);
    post.mockResolvedValue({
      error: { error: { code: "conflict", message: secret } },
      response: new Response(null, { status }),
    });
    await saveToken(user);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain(secret);
    expect(screen.getByLabelText("Token")).toHaveProperty("value", "");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(post).toHaveBeenCalledTimes(1);
    expect(cacheState(queryClient)).not.toContain(secret);
    await user.click(
      screen.getByRole("button", { name: "Cancel configuration" }),
    );
    post.mockResolvedValue(ok(fixtureRun()));
    await user.click(submit());
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]![1].body.calls.private.value).toEqual({
      status: "unknown",
    });
  },
);
it("cancels validation failures without a write", async () => {
  const { user } = setup(create);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("alert");
  expect(post).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Cancel configuration" }),
  );
  await user.click(submit());
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.calls.private.value).toEqual({
    status: "cancelled",
  });
});
it.each(["admin", "run"])(
  "requires %s for Webhook creation",
  async (permission) => {
    can.mockImplementation((verb: string) => verb !== permission);
    const { user } = setup(
      {
        action: "create",
        name: "Runs",
        url: "https://example.com/hook",
        kinds: ["run.completed"],
      },
      "configure_webhook",
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(post).not.toHaveBeenCalled();
  },
);
it.each(["update", "rotate", "revoke"])(
  "blocks %s of App-managed credentials",
  async (action) => {
    get.mockResolvedValue(ok({ ...saved, owner_app_id: "app_fixture" }));
    const { user } = setup({
      action,
      credential_id: credentialId,
      expected_version: `${credentialId}:1`,
    });
    await screen.findByText(
      "Configuration is unavailable. Review the resource page.",
    );
    await user.click(
      screen.getByRole("button", {
        name: action === "revoke" ? "Revoke credential" : "Save changes",
      }),
    );
    expect(post).not.toHaveBeenCalled();
    expect(patch).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  },
);
it("requests synchronization without claiming application and permits App-owned targets", async () => {
  get.mockResolvedValue(ok({ ...saved, owner_app_id: "app_fixture" }));
  const { user } = setup({ action: "sync", credential_id: credentialId });
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Sync active Sessions" })
        .closest("fieldset"),
    ).toHaveProperty("disabled", false),
  );
  await user.click(
    screen.getByRole("button", { name: "Sync active Sessions" }),
  );
  await screen.findByText(/^Saved\. Submit/);
  expect(post.mock.calls[0]![0]).toContain("/sync");
  expect(
    screen.getByText(/Active Sessions update asynchronously/),
  ).toBeTruthy();
});
it.each([
  { ...create, value: secret },
  { action: "rotate", credential_id: credentialId },
  { action: "destroy" },
])(
  "rejects malformed or secret-bearing proposals without a generic response fallback",
  async (request) => {
    const { user } = setup(request);
    expect(
      screen.getByText(/This configuration request is invalid/),
    ).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Response" })).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Cancel configuration" }),
    );
    expect(post).not.toHaveBeenCalled();
  },
);

it.each(["update", "revoke"])(
  "uses the original version for credential %s",
  async (action) => {
    const { user } = setup({
      action,
      credential_id: credentialId,
      expected_version: `${credentialId}:1`,
      ...(action === "update" ? { name: "Renamed" } : {}),
    });
    const label = action === "revoke" ? "Revoke credential" : "Save changes";
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: label }).closest("fieldset"),
      ).toHaveProperty("disabled", false),
    );
    await user.click(screen.getByRole("button", { name: label }));
    await screen.findByText(/^Saved\. Submit/);
    const calls = action === "update" ? patch.mock.calls : post.mock.calls;
    expect(calls[0]![1].headers).toEqual({ "If-Match": `"${credentialId}:1"` });
    if (action === "update")
      expect(calls[0]![1].body).toEqual({ name: "Renamed" });
  },
);
it("replaces a Webhook key privately while retaining the proposed version", async () => {
  get.mockResolvedValue(ok({ id: subscriptionId, name: "Runs", version: 9 }));
  patch.mockResolvedValue(ok({ id: subscriptionId, version: 10 }));
  const { user, queryClient } = setup(
    {
      action: "rotate",
      subscription_id: subscriptionId,
      expected_version: `${subscriptionId}:2`,
    },
    "configure_webhook",
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText("Signing secret").closest("fieldset"),
    ).toHaveProperty("disabled", false),
  );
  await user.type(screen.getByLabelText("Signing secret"), signingSecret);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText(/^Saved\. Submit/);
  expect(patch.mock.calls[0]![1]).toMatchObject({
    headers: { "If-Match": `"${subscriptionId}:2"` },
    body: { signing_secret: signingSecret },
  });
  expect(cacheState(queryClient)).not.toContain(signingSecret);
});
