// @vitest-environment jsdom
import { useEffect, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { MemoryRouter } from "react-router";
import { ModelFields } from "./model-editor";
import { AddModelButton } from "./models";

const mocks = vi.hoisted(() => ({
  prepare: vi.fn(),
  accountReady: false,
  groups: [] as { id: string; name: string; provider: string; accounts: [] }[],
  accountIdentity: "first-source",
  identityChanged: undefined as undefined | ((identity: string) => void),
  discover: vi.fn(async () => ({
    data: [{ value: "account-chat", label: "Account chat" }],
  })),
  put: vi.fn(async () => ({ data: {} })),
  apply: vi.fn(async (_body: unknown) => ({ data: { completed: true } })),
  get: vi.fn(async (path: string) => ({
    data:
      path === "/api/models/choices"
        ? {
            connections: [
              {
                id: "codex",
                label: "Codex subscription",
                provider: "openai-codex",
                authentication: "codex_subscription",
                default_model: "reviewed",
                models: [],
                account: {
                  provider: "codex",
                  label: "Codex",
                  login_methods: ["device", "browser"],
                },
              },
              {
                id: "grok-subscription",
                label: "Grok subscription",
                provider: "grok",
                authentication: "grok_subscription",
                default_model: "grok-reviewed",
                models: [],
                account: {
                  provider: "grok",
                  label: "Grok",
                  login_methods: ["device", "browser"],
                },
              },
              {
                id: "copilot-subscription",
                label: "GitHub Copilot subscription",
                provider: "github-copilot",
                authentication: "copilot_subscription",
                default_model: "",
                models: [],
                account: {
                  provider: "copilot",
                  label: "GitHub Copilot",
                  login_methods: ["device"],
                  model_discovery: true,
                },
              },
              {
                id: "grok",
                label: "Grok API",
                provider: "grok",
                authentication: "api_key",
                models: [],
                default_model: "grok-reviewed",
              },
              {
                id: "openai-responses",
                label: "API",
                provider: "openai-responses",
                authentication: "api_key",
                models: [],
                default_model: "reviewed",
                supports_base_url: true,
              },
            ],
          }
        : path === "/api/subscriptions"
          ? { accounts: [], groups: mocks.groups, adoption: {} }
          : path === "/api/auth/keys"
            ? [{ credential_ref: "key-saved" }]
            : { items: [], status: "unavailable" },
  })),
}));
vi.mock("../transport/context", () => ({
  useTransport: () => ({
    client: {
      GET: mocks.get,
      PUT: mocks.put,
      POST: (path: string, body: unknown) =>
        path === "/api/models/prepare"
          ? mocks.prepare(body)
          : path === "/api/setup/apply"
            ? mocks.apply(body)
            : path === "/api/auth/accounts/{provider}/models"
              ? mocks.discover()
              : Promise.resolve({
                  data: {
                    name: "Reviewed model",
                    presets: [
                      {
                        value: "high",
                        label: "High reasoning",
                        settings: { thinking: "high", max_tokens: 4000 },
                      },
                      {
                        value: "default",
                        label: "Provider defaults",
                        settings: {},
                      },
                    ],
                    context_choices: [
                      { value: 128000, label: "Standard" },
                      { value: 64000, label: "Compact" },
                    ],
                    native_tools: [],
                    reasoning_mode: { supported: true, state: "default" },
                  },
                }),
    },
  }),
}));
vi.mock("../setup/accounts", () => ({
  ProviderAccount: ({
    onReady,
    onIdentity,
  }: {
    onReady?: (ready: boolean) => void;
    onIdentity?: (identity: string) => void;
  }) => {
    useEffect(() => {
      onReady?.(mocks.accountReady);
      onIdentity?.(mocks.accountIdentity);
      mocks.identityChanged = onIdentity;
    }, [onReady, onIdentity]);
    return null;
  },
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.accountReady = false;
  mocks.groups = [];
});
const original = `# Model comment
schema_version: '1'
kind: model
id: model-edit
name: Existing
route: openai-responses:old-custom
authentication: {kind: api_key, credential_ref: key-saved}
settings:
  # Keep custom setting
  temperature: 0.3
  thinking: high
  max_tokens: 4000
model_characteristics:
  # Local budget
  context_window: 128000
  capabilities: []
`;
function mount(initial = original, onReady = vi.fn()) {
  function Editor() {
    const [source, setSource] = useState(initial);
    return (
      <>
        <ModelFields source={source} onChange={setSource} onReady={onReady} />
        <textarea
          aria-label="Advanced source"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        />
      </>
    );
  }
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false },
              mutations: { retry: false },
            },
          })
        }
      >
        <Editor />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
async function chooseModel(id: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  const search = await screen.findByPlaceholderText("Find a model");
  await user.type(search, id);
  await user.click(await screen.findByRole("option", { name: new RegExp(id) }));
}
function source() {
  return (screen.getByLabelText("Advanced source") as HTMLTextAreaElement)
    .value;
}
it.each([false, true])(
  "preserves comments and saves only the canonical context field (both fields: %s)",
  async (both) => {
    const initial = both
      ? original.replace(
          "context_window: 128000",
          "context_window: 128000\n  context_window_tokens: 128000",
        )
      : original;
    mount(initial);
    const user = userEvent.setup();
    await user.click(await screen.findByText("Model defaults"));
    await user.click(
      await screen.findByRole("combobox", { name: "Context budget" }),
    );
    await user.click(
      await screen.findByRole("option", { name: "Custom budget" }),
    );
    const input = await screen.findByLabelText("Working context budget");
    expect((input as HTMLInputElement).value).toBe("128000");
    expect(source()).toBe(initial);
    fireEvent.change(input, { target: { value: "64000" } });
    expect(parse(source()).model_characteristics).toEqual({
      context_window_tokens: 64000,
      capabilities: [],
    });
    expect(source()).toContain("# Local budget");
    expect(source()).toContain("# Keep custom setting");
    expect(parse(source()).settings).toEqual(parse(original).settings);
  },
);
it("provider defaults remove preset-owned fields without removing custom native settings", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByText("Model defaults"));
  const preset = await screen.findByRole("combobox", {
    name: "Settings preset",
  });
  await waitFor(() =>
    expect(preset.textContent).toContain("High reasoning (default)"),
  );
  const budget = screen.getByRole("combobox", { name: "Context budget" });
  expect(budget.textContent).toContain("128,000 tokens");
  await user.click(budget);
  await user.click(
    await screen.findByRole("option", { name: "Compact · 64,000 tokens" }),
  );
  expect(budget.textContent).toContain("64,000 tokens");
  await user.click(budget);
  await user.click(
    await screen.findByRole("option", { name: "Custom budget" }),
  );
  fireEvent.change(screen.getByLabelText("Working context budget"), {
    target: { value: "32000" },
  });
  expect(budget.textContent).toContain("Custom budget");
  await user.click(preset);
  expect(screen.queryByRole("option", { name: /Keep current/ })).toBeNull();
  await user.click(
    await screen.findByRole("option", { name: "Provider defaults" }),
  );
  expect(preset.textContent).toContain("Provider defaults");
  expect(parse(source()).settings).toEqual({ temperature: 0.3 });
  expect(source()).toContain("# Keep custom setting");
});
it("an old prepare cannot overwrite advanced source edits", async () => {
  let complete!: (value: unknown) => void;
  mocks.prepare.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount();
  await chooseModel("new-model");
  await waitFor(() => expect(mocks.prepare).toHaveBeenCalledTimes(1));
  const advanced = original
    .replace("old-custom", "advanced-new")
    .replace("temperature: 0.3", "temperature: 0.9");
  fireEvent.change(screen.getByLabelText("Advanced source"), {
    target: { value: advanced },
  });
  await act(async () =>
    complete({
      data: {
        route: "openai-responses:new-model",
        authentication: { kind: "api_key", credential_ref: "key-saved" },
        settings: {},
      },
    }),
  );
  expect(source()).toBe(advanced);
});

it("edits the permanent reasoning mode without clobbering effort, and removes only mode for provider default", async () => {
  const user = userEvent.setup();
  mount(
    original.replace(
      "  thinking: high",
      "  thinking: high\n  openai_reasoning_mode: pro\n  openai_reasoning_summary: detailed",
    ),
  );
  await user.click(await screen.findByText("Model defaults"));
  await user.click(
    await screen.findByRole("combobox", { name: "Reasoning mode" }),
  );
  await user.click(await screen.findByRole("option", { name: "Standard" }));
  expect(parse(source()).settings).toMatchObject({
    thinking: "high",
    openai_reasoning_mode: "standard",
    openai_reasoning_summary: "detailed",
  });
  await user.click(screen.getByRole("combobox", { name: "Settings preset" }));
  await user.click(
    await screen.findByRole("option", { name: "Provider defaults" }),
  );
  expect(parse(source()).settings.openai_reasoning_mode).toBe("standard");
  await user.click(screen.getByRole("combobox", { name: "Reasoning mode" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Provider default",
    }),
  );
  expect(parse(source()).settings).toEqual({
    temperature: 0.3,
    openai_reasoning_summary: "detailed",
  });
});

function mountAddModel() {
  const onSaved = vi.fn();
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false },
              mutations: { retry: false },
            },
          })
        }
      >
        <AddModelButton onSaved={onSaved} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return onSaved;
}

it("saves an unauthenticated subscription recipe independently of the Agent draft", async () => {
  mocks.prepare.mockResolvedValue({
    data: {
      route: "openai-codex:reviewed",
      authentication: { kind: "codex_subscription" },
      settings: {},
      model_configuration: {},
    },
  });
  const onSaved = mountAddModel();
  fireEvent.click(screen.getByRole("button", { name: "Add model" }));
  expect(screen.queryByRole("button", { name: "Use this model" })).toBeNull();
  const save = screen.getByRole("button", { name: "Save model" });
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  expect(
    screen.getByText(/Connect an account before running this model/),
  ).toBeTruthy();
  fireEvent.click(save);
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(mocks.apply).toHaveBeenCalledExactlyOnceWith({
    body: {
      selection: {
        model: {
          route: "openai-codex:reviewed",
          authentication: { kind: "codex_subscription" },
          settings: {},
          model_configuration: {},
        },
        new_model_id: onSaved.mock.calls[0]![0],
        new_model_name: "openai-codex:reviewed",
        environment_profile: "environment-native",
        enable_live: false,
      },
    },
  });
  expect(mocks.put).not.toHaveBeenCalled();
});

it("cannot save the previous recipe after changing the visible connection or model", async () => {
  mocks.prepare.mockResolvedValue({
    data: {
      route: "openai-codex:reviewed",
      authentication: { kind: "codex_subscription" },
      settings: {},
      model_configuration: {},
    },
  });
  mountAddModel();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add model" }));

  const save = screen.getByRole("button", {
    name: "Save model",
  }) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  mocks.prepare.mockImplementation(() => new Promise(() => {}));
  await chooseModel("different-model");
  await waitFor(() => expect(save.disabled).toBe(true));
  await chooseModel("reviewed");
  await waitFor(() => expect(save.disabled).toBe(false));
  await user.click(screen.getByRole("combobox", { name: "Model connection" }));
  await user.click(
    await screen.findByRole("option", { name: "Grok subscription" }),
  );
  await waitFor(() => expect(save.disabled).toBe(true));
  await user.click(save);
  expect(mocks.apply).not.toHaveBeenCalled();
});

const copilotSource = `schema_version: '1'
kind: model
id: model-copilot
name: Copilot
route: github-copilot:custom-chat
authentication: {kind: copilot_subscription}
settings: {}
`;

it("maps a saved Copilot recipe through shared declarations without login or catalog requests", async () => {
  mount(copilotSource);
  expect(
    (await screen.findByRole("combobox", { name: "Model connection" }))
      .textContent,
  ).toContain("GitHub Copilot subscription");
  expect(
    (
      (await screen.findByRole("button", {
        name: "Fetch account models",
      })) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(mocks.discover).not.toHaveBeenCalled();
  expect(source()).toBe(copilotSource);
  expect(mocks.prepare).not.toHaveBeenCalled();
});

it("only fetches account models on an explicit action and leaves the saved recipe untouched", async () => {
  mocks.accountReady = true;
  mount(copilotSource);
  const fetch = await screen.findByRole("button", {
    name: "Fetch account models",
  });
  await waitFor(() =>
    expect((fetch as HTMLButtonElement).disabled).toBe(false),
  );
  expect(mocks.discover).not.toHaveBeenCalled();
  fireEvent.click(fetch);
  await waitFor(() => expect(mocks.discover).toHaveBeenCalledOnce());
  expect(source()).toBe(copilotSource);
  expect(mocks.prepare).not.toHaveBeenCalled();
});

it("discards a catalog response after the ready account changes source", async () => {
  mocks.accountReady = true;
  let complete!: (value: { data: { value: string; label: string }[] }) => void;
  mocks.discover.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount(copilotSource);
  const fetch = await screen.findByRole("button", {
    name: "Fetch account models",
  });
  await waitFor(() =>
    expect((fetch as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(fetch);
  await waitFor(() => expect(mocks.discover).toHaveBeenCalledOnce());
  act(() => mocks.identityChanged?.("other-source:same-account"));
  await act(async () =>
    complete({ data: [{ value: "stale-model", label: "Stale model" }] }),
  );
  expect(screen.queryByText("Stale model")).toBeNull();
  expect(source()).toBe(copilotSource);
  expect(mocks.prepare).not.toHaveBeenCalled();
});

it("removes a completed account catalog immediately after an identity-only change", async () => {
  mocks.accountReady = true;
  mount(copilotSource);
  const fetch = await screen.findByRole("button", {
    name: "Fetch account models",
  });
  await waitFor(() =>
    expect((fetch as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(fetch);
  await waitFor(() => expect(mocks.discover).toHaveBeenCalledOnce());
  await userEvent.click(screen.getByRole("combobox", { name: "Model" }));
  await screen.findByRole("option", { name: /Account chat/ });
  act(() => mocks.identityChanged?.("other-source:same-account"));
  await waitFor(() =>
    expect(screen.queryByRole("option", { name: /Account chat/ })).toBeNull(),
  );
});

it.each([
  [undefined, undefined, "/settings/accounts?tab=groups"],
  [
    undefined,
    "codex-default",
    "/settings/accounts?tab=groups&group=codex-default",
  ],
  [
    "codex-work",
    "codex-work",
    "/settings/accounts?tab=groups&group=codex-work",
  ],
])(
  "links group management to the resolved group (%s, %s)",
  async (selected, existing, expected) => {
    mocks.groups = existing
      ? [{ id: existing, name: "Work group", provider: "codex", accounts: [] }]
      : [];
    mount(`schema_version: '1'
kind: model
id: model-subscription
name: Subscription
route: openai-codex:reviewed
authentication: {kind: codex_subscription${selected ? `, group: ${selected}` : ""}}
`);
    await waitFor(() =>
      expect(
        screen
          .getByRole("link", { name: "Manage accounts and groups" })
          .getAttribute("href"),
      ).toBe(expected),
    );
  },
);

it("restores the API connection when discarding a same-route subscription change", async () => {
  const initial = original.replace(
    "openai-responses:old-custom",
    "grok:grok-reviewed",
  );
  mocks.prepare.mockResolvedValue({
    data: {
      route: "grok:grok-reviewed",
      authentication: { kind: "grok_subscription" },
      model_configuration: {},
    },
  });
  mount(initial);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Model connection" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Grok subscription" }),
  );
  await waitFor(() =>
    expect(parse(source()).authentication.kind).toBe("grok_subscription"),
  );
  fireEvent.change(screen.getByLabelText("Advanced source"), {
    target: { value: initial },
  });
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Model connection" }).textContent,
    ).toContain("Grok API"),
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 250));
  });
  expect(source()).toBe(initial);
  expect(mocks.prepare).toHaveBeenCalledTimes(1);
});

it.each(["openai:gpt-5", "google-cloud:gemini-2.5-pro"])(
  "keeps advanced-only route %s saveable without normalizing it",
  async (route) => {
    const initial = original.replace("openai-responses:old-custom", route);
    const ready = vi.fn();
    mount(initial, ready);
    await waitFor(() => expect(ready).toHaveBeenLastCalledWith(true));
    const edited = initial.replace("name: Existing", "name: Renamed");
    fireEvent.change(screen.getByLabelText("Advanced source"), {
      target: { value: edited },
    });
    expect(source()).toBe(edited);
    expect(mocks.prepare).not.toHaveBeenCalled();
    expect(screen.queryByText("Restore recommended defaults")).toBeNull();
  },
);

it("clears API endpoint configuration on a subscription transition", async () => {
  mocks.prepare.mockResolvedValue({
    data: {
      route: "openai-codex:reviewed",
      authentication: { kind: "codex_subscription" },
      model_configuration: {},
    },
  });
  mount(
    `${original}model_configuration:\n  base_url: https://gateway.example/v1\n  session_affinity_header: X-Session-Id\n`,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Model connection" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Codex subscription" }),
  );
  await waitFor(() =>
    expect(parse(source()).route).toBe("openai-codex:reviewed"),
  );
  expect(parse(source()).model_configuration).toEqual({});
});

it("preserves custom API configuration when changing models", async () => {
  mocks.prepare.mockResolvedValue({
    data: {
      route: "openai-responses:new-model",
      authentication: { kind: "api_key", credential_ref: "key-saved" },
      model_configuration: {},
    },
  });
  mount(
    `${original}model_configuration:\n  session_affinity_header: X-Session-Id\n`,
  );
  await chooseModel("new-model");
  await waitFor(() =>
    expect(parse(source()).route).toBe("openai-responses:new-model"),
  );
  expect(parse(source()).model_configuration).toEqual({
    session_affinity_header: "X-Session-Id",
  });
});
