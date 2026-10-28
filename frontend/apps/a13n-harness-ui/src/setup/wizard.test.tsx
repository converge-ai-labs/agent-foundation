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
  defaults: { environment_profile: "environment-native", shell_review: true },
};
const json = (body: unknown, code = 200) =>
  new Response(JSON.stringify(body), {
    status: code,
    headers: { "Content-Type": "application/json" },
  });
const files = {
  "config.yaml": "defaults: {agent: agent-primary}\n",
  "agents/codex.yaml": "id: agent-primary\n",
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
  if (
    path.startsWith("/api/auth/accounts/") ||
    path.startsWith("/api/subscriptions/accounts/")
  )
    return json({
      usable: true,
      availability: "available",
      required_action: "none",
      account_id: "account-test",
      source: "file",
      expiry: "valid",
      shared_with_cli: false,
    });
  if (path === "/api/auth/keys") {
    if (request.method === "PUT") {
      keyReferences.push(body.credential_ref);
      return json({ credential_ref: body.credential_ref });
    }
    return json(keyReferences.map((credential_ref) => ({ credential_ref })));
  }
  if (path === "/api/models/choices")
    return json({
      connections: [
        {
          id: "codex",
          label: "Codex subscription",
          provider: "openai-codex",
          authentication: "codex_subscription",
          account: {
            provider: "codex",
            label: "Codex",
            login_methods: ["device", "browser"],
          },
          models: [{ value: "gpt-5.6-sol", label: "Release Codex model" }],
          default_model: "gpt-5.6-sol",
        },
        {
          id: "openai-responses",
          label: "OpenAI API",
          provider: "openai-responses",
          authentication: "api_key",
          base_url: "https://api.openai.com/v1",
          supports_base_url: true,
          supports_session_affinity: true,
          models: [{ value: "gpt-5.4", label: "GPT-5.4" }],
          default_model: "gpt-5.4",
        },
      ],
      session_affinity_presets: [
        {
          label: "LiteLLM",
          header: "x-litellm-session-id",
          description: "Enable session affinity on your gateway first.",
        },
      ],
    });
  if (path === "/api/models/catalog")
    return json({ items: [], status: "unavailable" });
  if (path === "/api/models/options")
    return json({
      name: body.model_id,
      route: `${body.connection}:${body.model_id}`,
      presets: [
        { value: "high", label: "High", settings: { thinking: "high" } },
      ],
      context_window: 350000,
      known_context_window: null,
      native_tools: [],
      shell_review_models: [
        { value: "gpt-5.4-mini", label: "GPT-5.4 mini" },
        { value: "gpt-5.4", label: "GPT-5.4" },
      ],
    });
  if (path === "/api/models/prepare")
    return json({
      route: `${body.connection === "codex" ? "openai-codex" : body.connection}:${body.model_id}`,
      authentication: body.authentication ?? { kind: "codex_subscription" },
      model_configuration: body.base_url ? { base_url: body.base_url } : {},
      settings: { thinking: "high" },
      model_characteristics: { context_window_tokens: 350000 },
    });
  if (path === "/api/envd")
    return json({ installed: false, phase: "idle", source: "managed" });
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
      default_agent: published ? "agent-primary" : null,
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
              agent_source: { kind: "agent", id: "agent-primary" },
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
            <Route path="/threads/:threadId" element={<p>First chat</p>} />
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
  await chooseModel();
  await continueStep();
  await screen.findByText("Generated files (2)");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save and start",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
}

it("keeps a connected account ready when reselecting the current connection", async () => {
  mount();
  await screen.findByText(/Account: account-test/);
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Model connection" }));
  await user.click(screen.getByRole("option", { name: "Codex subscription" }));
  await chooseModel();
  await continueStep();
  expect(await screen.findByText("Decide where your agent works")).toBeTruthy();
});

it("uses server model choices, saves, reconciles and creates exactly one chat without sending input", async () => {
  mount();
  await reviewConfiguration();
  expect(
    screen.getByText(/Automatic memory updates use model quota/),
  ).toBeTruthy();
  const identity = readWizardDraft(status.draft_scope!)!.threadId;
  expect(identity).toMatch(/^thread_[0-9a-f]{32}$/);
  expect(
    screen.queryByRole("button", { name: "Review configuration" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save and start" }));
  await screen.findByText("First chat");
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
      version: 2,
      step: 0,
      selection: { ...status.defaults!, default_agent: "agent-primary" },
      threadId: identity,
    });
    expect(readWizardDraft(status.draft_scope!)?.threadId).toBe(identity);
    mount();
    await reviewConfiguration();
    fireEvent.click(screen.getByRole("button", { name: "Save and start" }));
    await screen.findByText("First chat");
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
  fireEvent.click(screen.getByRole("button", { name: "Save and start" }));
  await screen.findByText("First chat");
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
  fireEvent.click(screen.getByRole("button", { name: "Save and start" }));
  await screen.findByText("Not saved");
  expect(readWizardDraft(status.draft_scope!)?.pending?.files).toEqual(files);
  view.unmount();
  mount();
  await screen.findByText("Resume your saved setup");
  expect(calls.filter((c) => c.path === "/api/setup/apply")).toHaveLength(1);
  expect(calls.some((c) => c.path === "/api/threads")).toBe(false);
});

it("automatically checks Sandbox after selection, reports failure and never downgrades it", async () => {
  override = async (request) => {
    if (new URL(request.url).pathname === "/api/environments/preflight")
      return json({
        ready: false,
        message: "Isolation unavailable",
        instructions: ["Fix the host or explicitly select Full Control."],
      });
  };
  mount();
  await chooseModel();
  await continueStep();
  fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
  await screen.findByText("Generated files (2)");
  expect(
    (
      screen.getByRole("button", {
        name: "Save and start",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await screen.findByText("Isolation unavailable");
  expect(
    readWizardDraft(status.draft_scope!)?.selection.environment_profile,
  ).toBe("environment-sandbox");
  expect(calls.some((c) => c.path === "/api/setup/apply")).toBe(false);
});

it("retains nonsecret choices on refresh and dismissal, never the API key input", async () => {
  const view = mount();
  await selectApi();
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "never-store-this-secret" },
  });
  await waitFor(() =>
    expect(readWizardDraft(status.draft_scope!)).toBeTruthy(),
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
        account_id: "acct-codex-default",
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

it("does not complete recovery into an existing chat with different execution authority", async () => {
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
    step: 1,
    pending: {
      selection: {
        ...draft.selection,
        default_agent: "agent-primary",
        environment_profile: "environment-sandbox",
      },
      files,
    },
  });
  mount();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Resume setup",
    }),
  );
  await screen.findByText(/first chat already uses different settings/);
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
  await selectApi();
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "fake-test-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
  const input = await screen.findByLabelText("Base URL");
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
  await selectApi();
  fireEvent.change(await screen.findByLabelText("Provider API key"), {
    target: { value: "test-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Continue",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(await screen.findByRole("tab", { name: "Model defaults" }));
  const affinity = screen.getByRole("combobox", {
    name: "Gateway session affinity",
  });
  await user.click(affinity);
  await user.click(await screen.findByRole("option", { name: /LiteLLM/ }));
  expect(
    readWizardDraft(status.draft_scope!)?.selection.model?.model_configuration
      ?.session_affinity_header,
  ).toBe("x-litellm-session-id");

  await user.click(affinity);
  await user.click(
    await screen.findByRole("option", { name: "Custom header" }),
  );
  const input = (await screen.findByLabelText(
    "Session affinity header",
  )) as HTMLInputElement;
  expect(input.value).toBe("x-litellm-session-id");
  fireEvent.change(input, { target: { value: "x-company-session" } });
  await continueStep();
  const model = readWizardDraft(status.draft_scope!)?.selection.model;
  expect(model?.model_configuration).toEqual({
    base_url: "https://api.openai.com/v1",
    session_affinity_header: "x-company-session",
  });
  expect(model?.settings).not.toHaveProperty("extra_headers");
});

it.each([null, "custom/reviewer"])(
  "explicitly chooses or skips the API reviewer (%s) and retains the choice on refresh",
  async (reviewer) => {
    saveWizardDraft(status.draft_scope!, {
      version: 2,
      step: 1,
      threadId: `thread_${"a".repeat(32)}`,
      selection: {
        environment_profile: "environment-native",
        default_agent: "agent-primary",
        shell_review: false,
        model: {
          route: "openai-responses:gpt-5.4",
          authentication: { kind: "api_key", env: "TEST_KEY" },
          model_configuration: { base_url: "https://gateway.example/v1" },
        },
      },
    });
    const first = mount();
    const input = await screen.findByLabelText("Review model ID (optional)");
    expect((input as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/No model selected/)).toBeTruthy();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("combobox", { name: "Shell review model" }),
    );
    await user.click(
      await screen.findByRole("option", { name: /GPT-5.4 mini/ }),
    );
    expect((input as HTMLInputElement).value).toBe("gpt-5.4-mini");
    fireEvent.change(input, { target: { value: reviewer ?? "" } });
    fireEvent.click(
      screen.getByRole("button", {
        name: reviewer ? "Continue" : "Skip shell review",
      }),
    );
    await screen.findByText("Decide where your agent works");
    expect(
      readWizardDraft(status.draft_scope!)?.selection.shell_review_model,
    ).toBe(reviewer);
    first.unmount();
    mount();
    await screen.findByText("Decide where your agent works");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(
      (
        (await screen.findByLabelText(
          "Review model ID (optional)",
        )) as HTMLInputElement
      ).value,
    ).toBe(reviewer ?? "");
    fireEvent.click(
      screen.getByRole("button", {
        name: reviewer ? "Continue" : "Skip shell review",
      }),
    );
    await waitFor(() =>
      expect(calls.some((call) => call.path === "/api/setup/preview")).toBe(
        true,
      ),
    );
    const body = calls.findLast(
      (call) => call.path === "/api/setup/preview",
    )!.body!;
    expect(body.shell_review_model).toBe(reviewer);
    expect(body.shell_review).toBe(!!reviewer);
  },
);

async function selectApi() {
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Model connection" }),
  );
  await user.click(await screen.findByRole("option", { name: "OpenAI API" }));
}

async function chooseModel() {
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Continue",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(await screen.findByText(/Account: account-test/)).toBeTruthy();
}

it("shows download, verification and isolation progress; cancels and retries before saving Sandbox", async () => {
  let complete: ((value: Response) => void) | undefined;
  let phase = "downloading";
  let aborted = false;
  override = async (request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/envd")
      return json({
        phase,
        installed: phase === "idle",
        downloaded_bytes: 1048576,
        total_bytes: 2097152,
      });
    if (path === "/api/environments/preflight")
      return new Promise((resolve, reject) => {
        complete = resolve;
        request.signal.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    if (created && path.startsWith("/api/threads/"))
      return json({
        thread: {
          thread_id: creationId,
          configuration: {
            agent_source: { kind: "agent", id: "agent-primary" },
            environment_profile_id: "environment-sandbox",
            project_id: null,
          },
        },
      });
  };
  mount();
  await reviewConfiguration();
  fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
  await screen.findByRole("progressbar", { name: "envd download" });
  expect(
    screen.getAllByText("Downloading envd… 1 MB / 2 MB").length,
  ).toBeGreaterThan(0);
  expect(
    (
      screen.getByRole("button", {
        name: "Save and start",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Cancel preparation" }));
  await waitFor(() => expect(aborted).toBe(true));
  expect(screen.getAllByText(/Preparation cancelled/).length).toBeGreaterThan(
    0,
  );
  phase = "verifying";
  fireEvent.click(screen.getByRole("button", { name: "Retry sandbox" }));
  await waitFor(
    () =>
      expect(
        screen.getAllByText("Verifying envd download…").length,
      ).toBeGreaterThan(0),
    { timeout: 3000 },
  );
  phase = "idle";
  await waitFor(
    () =>
      expect(
        screen.getAllByText("Checking Sandbox isolation…").length,
      ).toBeGreaterThan(0),
    { timeout: 3000 },
  );
  complete!(
    json({ ready: true, message: "Isolation ready", instructions: [] }),
  );
  await screen.findAllByText("Ready to save");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save and start",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save and start" }));
  await screen.findByText("First chat");
  expect(calls.filter((call) => call.path === "/api/setup/apply")).toHaveLength(
    1,
  );
}, 10000);

it("cancels obsolete Sandbox checks when changing roots or returning to Direct", async () => {
  const aborted: string[] = [];
  override = async (request) => {
    if (new URL(request.url).pathname === "/api/setup/preview") {
      const selection = await request.json();
      return json({
        files,
        project_paths: [selection.project_path || "/tmp/staging"],
      });
    }
    if (new URL(request.url).pathname === "/api/environments/preflight") {
      const { project_path } = await request.clone().json();
      return new Promise((_, reject) =>
        request.signal.addEventListener("abort", () => {
          aborted.push(project_path);
          reject(new DOMException("Aborted", "AbortError"));
        }),
      );
    }
  };
  mount();
  await reviewConfiguration();
  fireEvent.click(screen.getByRole("button", { name: "Sandbox" }));
  await screen.findByRole("button", { name: "Cancel preparation" });
  fireEvent.change(screen.getByLabelText("Project directory (optional)"), {
    target: { value: "/other" },
  });
  await waitFor(() => expect(aborted).toContain("/tmp/staging"));
  await waitFor(() =>
    expect(
      calls.filter((call) => call.path === "/api/environments/preflight"),
    ).toHaveLength(2),
  );
  fireEvent.click(screen.getByRole("button", { name: "Full Control" }));
  await waitFor(() => expect(aborted).toContain("/other"));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save and start",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(calls.some((call) => call.path === "/api/setup/apply")).toBe(false);
});

it("shows configuration validation failures and recovers after correcting the Project directory", async () => {
  override = async (request) => {
    if (
      new URL(request.url).pathname === "/api/setup/preview" &&
      (await request.json()).project_path === "/missing"
    )
      return json(
        {
          error: {
            code: "project_missing",
            message: "Project directory does not exist",
          },
        },
        400,
      );
  };
  mount();
  await reviewConfiguration();
  fireEvent.change(screen.getByLabelText("Project directory (optional)"), {
    target: { value: "/missing" },
  });
  await screen.findByText("Project directory does not exist");
  expect(
    (
      screen.getByRole("button", {
        name: "Save and start",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("Project directory (optional)"), {
    target: { value: "" },
  });
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save and start",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(screen.queryByText("Project directory does not exist")).toBeNull();
});
