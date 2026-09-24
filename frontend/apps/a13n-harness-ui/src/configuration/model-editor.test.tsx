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
import { ModelFields } from "./model-editor";
import { AddModelButton } from "./models";

const mocks = vi.hoisted(() => ({
  prepare: vi.fn(),
  accountReady: false,
  accountIdentity: "first-source",
  identityChanged: undefined as undefined | ((identity: string) => void),
  discover: vi.fn(async () => ({
    data: [{ value: "account-chat", label: "Account chat" }],
  })),
  put: vi.fn(async () => ({ data: {} })),
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
function mount(initial = original) {
  function Editor() {
    const [source, setSource] = useState(initial);
    return (
      <>
        <ModelFields source={source} onChange={setSource} />
        <textarea
          aria-label="Advanced source"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        />
      </>
    );
  }
  render(
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
    </QueryClientProvider>,
  );
}
function source() {
  return (screen.getByLabelText("Advanced source") as HTMLTextAreaElement)
    .value;
}
it("preserves YAML comments, empty capabilities and the authored context alias", async () => {
  mount();
  const input = await screen.findByLabelText("Working context budget");
  expect((input as HTMLInputElement).value).toBe("128000");
  expect(source()).toBe(original);
  fireEvent.change(input, { target: { value: "64000" } });
  expect(parse(source()).model_characteristics).toEqual({
    context_window: 64000,
    capabilities: [],
  });
  expect(source()).toContain("# Local budget");
  expect(source()).toContain("# Keep custom setting");
  expect(parse(source()).settings).toEqual(parse(original).settings);
});
it("provider defaults remove preset-owned fields without removing custom native settings", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Settings preset" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Provider defaults" }),
  );
  expect(parse(source()).settings).toEqual({ temperature: 0.3 });
  expect(source()).toContain("# Keep custom setting");
});
it("an old prepare cannot overwrite advanced edits after route-triggered remount", async () => {
  let complete!: (value: unknown) => void;
  mocks.prepare.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Model ID"), {
    target: { value: "new-model" },
  });
  const button = await screen.findByRole("button", {
    name: "Apply connection & defaults",
  });
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(button);
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
    </QueryClientProvider>,
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
  const useModel = await screen.findByRole("button", {
    name: "Use this model",
  });
  await waitFor(() =>
    expect((useModel as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(useModel);
  const save = screen.getByRole("button", { name: "Save model & select" });
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  expect(
    screen.getByText(/Connect its account before running it/),
  ).toBeTruthy();
  fireEvent.click(save);
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(mocks.put).toHaveBeenCalledTimes(1);
  const [path, request] = mocks.put.mock.calls[0] as unknown as [
    string,
    { body: { content: string }; params: { path: { relative_path: string } } },
  ];
  expect(path).toBe("/api/configuration/sources/{relative_path}");
  expect(request.params.path.relative_path).toMatch(/^models\/model-.+\.yaml$/);
  expect(parse(request.body.content).route).toBe("openai-codex:reviewed");
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
  const useModel = await screen.findByRole("button", {
    name: "Use this model",
  });
  await waitFor(() =>
    expect((useModel as HTMLButtonElement).disabled).toBe(false),
  );
  await user.click(useModel);
  const save = screen.getByRole("button", {
    name: "Save model & select",
  }) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Model ID"), {
    target: { value: "different-model" },
  });
  await waitFor(() => expect(save.disabled).toBe(true));
  fireEvent.change(screen.getByLabelText("Model ID"), {
    target: { value: "reviewed" },
  });
  await waitFor(() => expect(save.disabled).toBe(false));
  await user.click(screen.getByRole("combobox", { name: "Model connection" }));
  await user.click(screen.getByRole("option", { name: "Grok subscription" }));
  await waitFor(() => expect(save.disabled).toBe(true));
  await user.click(save);
  expect(mocks.put).not.toHaveBeenCalled();
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
