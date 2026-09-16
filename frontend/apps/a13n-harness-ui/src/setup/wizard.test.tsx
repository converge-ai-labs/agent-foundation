// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";
import { SetupWizard } from "./wizard";
import {
  readWizardDraft,
  saveWizardDraft,
  wizardDismissed,
} from "./wizard-state";

const status: Schema<"SetupStatus"> = {
  needed: true,
  fresh: true,
  draft_scope: "test-installation",
  configuration_path: "/tmp/config.yaml",
  providers: [
    { provider: "codex", available: true, selected: true, action: "none" },
  ],
  agents: {},
  projects: {},
  default_agent: null,
  default_project: null,
  environment_profile: "environment-native",
  choices: {
    defaults: {
      environment_profile: "environment-native",
      codex_model: "gpt-5.6-sol",
      grok_model: "grok-4.6",
      codex_thinking: "high",
      codex_context_window: 350000,
      shell_review: true,
    },
    session_affinity_presets: [
      {
        label: "LiteLLM",
        header: "x-litellm-session-id",
        description: "Enable session affinity on your gateway first.",
      },
    ],
    subscription_models: {
      codex: [{ value: "gpt-5.6-sol", label: "Release Codex model" }],
      grok: [{ value: "grok-4.6", label: "Release Grok model" }],
    },
    api_providers: [
      {
        value: "openai-responses",
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        models: ["gpt-5.4"],
        supports_session_affinity: true,
      },
    ],
  },
};
const json = (body: unknown, code = 200) =>
  new Response(JSON.stringify(body), {
    status: code,
    headers: { "Content-Type": "application/json" },
  });
const files = {
  "config.yaml": "defaults: {agent: agent-codex}\n",
  "agents/codex.yaml": "id: agent-codex\n",
};
let published = false;
let created = false;
let creationId = "";
let keyReferences: string[] = [];
let calls: { path: string; method: string; body?: Record<string, unknown> }[] =
  [];
let override: ((request: Request) => Promise<Response | undefined>) | undefined;
async function fetcher(request: Request): Promise<Response> {
  const path = decodeURIComponent(new URL(request.url).pathname);
  const body =
    request.method === "GET" || request.method === "DELETE"
      ? undefined
      : await request.clone().json();
  calls.push({ path, method: request.method, body });
  const response = await override?.(request);
  if (response) return response;
  if (path === "/api/auth/logins") return json(null);
  if (path.startsWith("/api/auth/accounts/"))
    return json({
      usable: true,
      availability: "available",
      required_action: "none",
    });
  if (path === "/api/auth/keys") {
    if (request.method === "PUT") {
      keyReferences.push(body.credential_ref);
      return json({ credential_ref: body.credential_ref });
    }
    return json(keyReferences.map((credential_ref) => ({ credential_ref })));
  }
  if (path === "/api/setup/model-options")
    return json({
      presets: [
        { value: "high", label: "High", settings: { thinking: "high" } },
      ],
      context_window: 350000,
      known_context_window: null,
    });
  if (path === "/api/setup/preview")
    return json({
      files,
      preserved_paths: [],
      project_paths: ["/tmp/staging"],
    });
  if (path === "/api/setup/apply") {
    published = true;
    return json({ completed: true, published_paths: Object.keys(files) });
  }
  if (path.startsWith("/api/configuration/sources/"))
    return published
      ? json({
          content: files[path.split("/sources/")[1] as keyof typeof files],
        })
      : json(
          { error: { code: "source_not_found", message: "Not saved" } },
          404,
        );
  if (path === "/api/setup")
    return json({
      ...status,
      needed: !published,
      fresh: !published,
      default_agent: published ? "agent-codex" : null,
    });
  if (path === "/api/threads" && request.method === "POST") {
    created = true;
    creationId = body.thread_id;
    return json({ thread_id: creationId });
  }
  if (path.startsWith("/api/threads/"))
    return created
      ? json({
          thread: {
            thread_id: creationId,
            configuration: {
              agent_source: { kind: "agent", id: "agent-codex" },
              environment_profile_id: "environment-native",
              project_id: null,
            },
          },
        })
      : json(
          {
            error: { code: "thread_missing", message: "Thread does not exist" },
          },
          400,
        );
  throw new Error(`Unexpected request: ${request.method} ${path}`);
}
function mount(selected = status) {
  const queries = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false, networkMode: "always" },
    },
  });
  const transport = createTransport("", () => {});
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <BrowserRouter>
          <Routes>
            <Route path="/setup" element={<SetupWizard status={selected} />} />
            <Route
              path="/threads/:threadId"
              element={<p>First conversation</p>}
            />
            <Route path="/" element={<p>Workbench</p>} />
          </Routes>
        </BrowserRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  localStorage.clear();
  calls = [];
  published = false;
  created = false;
  creationId = "";
  keyReferences = [];
  override = undefined;
  window.history.replaceState(null, "", "/setup");
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal("fetch", vi.fn(fetcher));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function continueStep() {
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}
async function reviewConfiguration() {
  await continueStep();
  await continueStep();
  fireEvent.click(screen.getByRole("button", { name: "Review configuration" }));
  await screen.findByText("Generated files (2)");
}

it("uses server model choices, saves, reconciles and creates exactly one conversation without sending input", async () => {
  mount();
  await reviewConfiguration();
  const identity = readWizardDraft(status.draft_scope!)!.threadId;
  expect(identity).toMatch(/^thread_[0-9a-f]{32}$/);
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start chatting" }),
  );
  await screen.findByText("First conversation");
  expect(creationId).toBe(identity);
  expect(
    calls.filter((c) => c.path === "/api/threads" && c.method === "POST"),
  ).toHaveLength(1);
  expect(
    calls.some(
      (c) => c.path.includes("submit") || c.path.includes("preflight"),
    ),
  ).toBe(false);
  expect(readWizardDraft(status.draft_scope!)).toBeUndefined();
});

it.each(["_", "-"])(
  "restores a retained thread%s identity without reallocating it",
  async (separator) => {
    const identity = `thread${separator}${"a".repeat(32)}`;
    saveWizardDraft(status.draft_scope!, {
      version: 1,
      step: 0,
      connection: "codex",
      selection: status.choices!.defaults,
      apiProvider: "openai-responses",
      modelId: "gpt-5.4",
      baseUrl: "https://api.openai.com/v1",
      preset: "",
      credential: "",
      threadId: identity,
    });
    expect(readWizardDraft(status.draft_scope!)?.threadId).toBe(identity);
    mount();
    await reviewConfiguration();
    fireEvent.click(
      screen.getByRole("button", { name: "Save and start chatting" }),
    );
    await screen.findByText("First conversation");
    expect(creationId).toBe(identity);
  },
);

it("recovers lost publication and creation acknowledgements through exact reads, without duplicate writes", async () => {
  override = async (request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/setup/apply") {
      published = true;
      throw new TypeError("Lost publication response");
    }
    if (path === "/api/threads" && request.method === "POST") {
      created = true;
      creationId = (await request.json()).thread_id;
      throw new TypeError("Lost creation response");
    }
    return undefined;
  };
  mount();
  await reviewConfiguration();
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start chatting" }),
  );
  await screen.findByText("First conversation");
  expect(calls.filter((c) => c.path === "/api/setup/apply")).toHaveLength(1);
  expect(calls.filter((c) => c.path === "/api/threads")).toHaveLength(1);
});

it("keeps partial publication pending across reload and never automatically retries", async () => {
  override = async (request) => {
    if (new URL(request.url).pathname === "/api/setup/apply")
      return json({
        completed: false,
        error_message: "Partial save",
        published_paths: [],
      });
  };
  const view = mount();
  await reviewConfiguration();
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start chatting" }),
  );
  await screen.findByText("Not saved");
  expect(readWizardDraft(status.draft_scope!)?.pending?.files).toEqual(files);
  view.unmount();
  mount();
  await screen.findByText("Resume your saved setup");
  expect(calls.filter((c) => c.path === "/api/setup/apply")).toHaveLength(1);
  expect(calls.some((c) => c.path === "/api/threads")).toBe(false);
});

it("requires a successful explicit Sandbox preflight and never downgrades it", async () => {
  override = async (request) => {
    if (new URL(request.url).pathname === "/api/environments/preflight")
      return json({
        ready: false,
        message: "Isolation unavailable",
        instructions: ["Fix the host or explicitly select Full Control."],
      });
  };
  mount();
  await continueStep();
  await continueStep();
  fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
  fireEvent.click(screen.getByRole("button", { name: "Review configuration" }));
  await screen.findByText("Generated files (2)");
  expect(
    (
      screen.getByRole("button", {
        name: "Save and start chatting",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(
    screen.getByRole("button", { name: "Check Sandbox readiness" }),
  );
  await screen.findByText("Isolation unavailable");
  expect(
    readWizardDraft(status.draft_scope!)?.selection.environment_profile,
  ).toBe("environment-sandbox");
  expect(calls.some((c) => c.path === "/api/setup/apply")).toBe(false);
});

it("retains nonsecret choices on refresh and dismissal, never the API key input", async () => {
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "API key" }));
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "never-store-this-secret" },
  });
  await waitFor(() =>
    expect(readWizardDraft(status.draft_scope!)?.connection).toBe("api_key"),
  );
  expect(JSON.stringify(localStorage)).not.toContain("never-store-this-secret");
  view.unmount();
  mount();
  expect(
    ((await screen.findByLabelText("Provider API key")) as HTMLInputElement)
      .value,
  ).toBe("");
  expect(readWizardDraft("different-installation")).toBeUndefined();
  fireEvent.click(screen.getAllByRole("button", { name: "Set up later" })[0]!);
  await screen.findByText("Workbench");
  expect(wizardDismissed(status.draft_scope!)).toBe(true);
});

it("resumes the server's active device login without starting another authorization", async () => {
  override = async (request) => {
    const path = new URL(request.url).pathname;
    if (
      path === "/api/auth/logins" ||
      path === "/api/auth/logins/login-restored"
    )
      return json({
        session_id: "login-restored",
        provider: "codex",
        method: "device",
        state: "waiting",
        verification_url: "https://example.com/device",
        user_code: "ABCD-1234",
      });
  };
  mount();
  await screen.findByText("ABCD-1234");
  expect(
    screen.getByRole("link", { name: /https:\/\/example\.com\/device/ }),
  ).toBeTruthy();
  expect(
    calls.filter((c) => c.path === "/api/auth/logins" && c.method === "POST"),
  ).toHaveLength(0);
  expect(JSON.stringify(localStorage)).not.toContain("ABCD-1234");
});

it("does not complete recovery into an existing conversation with different execution authority", async () => {
  const first = mount();
  await waitFor(() =>
    expect(readWizardDraft(status.draft_scope!)).toBeTruthy(),
  );
  const draft = readWizardDraft(status.draft_scope!)!;
  first.unmount();
  published = true;
  created = true;
  creationId = draft.threadId;
  saveWizardDraft(status.draft_scope!, {
    ...draft,
    step: 2,
    pending: {
      selection: {
        ...draft.selection,
        default_agent: "agent-codex",
        environment_profile: "environment-sandbox",
      },
      files,
    },
  });
  mount();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Check saved setup and open conversation",
    }),
  );
  await screen.findByText(
    /first conversation already exists with different choices/,
  );
  expect(window.location.pathname).toBe("/setup");
  expect(readWizardDraft(status.draft_scope!)?.pending).toBeTruthy();
  expect(
    calls.some(
      (call) => call.path === "/api/threads" && call.method === "POST",
    ),
  ).toBe(false);
});

it("never persists credentials embedded in an endpoint, even before backend validation returns", async () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "API key" }));
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "fake-test-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
  await continueStep();
  const input = await screen.findByLabelText("API base URL");
  for (const url of [
    "https://user:embedded-secret@example.com/v1",
    "https://example.com/v1?api_key=embedded-secret",
  ]) {
    fireEvent.change(input, { target: { value: url } });
    expect((input as HTMLInputElement).value).toBe(url);
    expect(JSON.stringify(localStorage)).not.toContain("embedded-secret");
  }
});

it("offers affinity presets and captures a custom replacement alongside the endpoint", async () => {
  mount();
  const user = userEvent.setup();
  fireEvent.click(screen.getByRole("button", { name: "API key" }));
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "test-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
  await continueStep();
  const input = (await screen.findByLabelText(
    "Session affinity header",
  )) as HTMLInputElement;
  expect(input.value).toBe("");
  await user.click(
    screen.getByRole("combobox", { name: "Gateway session affinity" }),
  );
  await user.click(await screen.findByRole("option", { name: /LiteLLM/ }));
  expect(input.value).toBe("x-litellm-session-id");
  expect(
    screen.getByText("Enable session affinity on your gateway first."),
  ).toBeTruthy();
  fireEvent.change(input, { target: { value: "x-company-session" } });
  await continueStep();
  const model = readWizardDraft(status.draft_scope!)?.selection.api_key_model;
  expect(model?.model_configuration).toEqual({
    base_url: "https://api.openai.com/v1",
    session_affinity_header: "x-company-session",
  });
  expect(model?.settings).not.toHaveProperty("extra_headers");
});
