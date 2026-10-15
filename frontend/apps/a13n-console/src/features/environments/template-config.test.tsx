import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { TemplateConfig } from "./template-config";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  close: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET, POST: state.POST, PATCH: state.PATCH },
    workspace: () => ({ GET: state.GET, POST: state.POST, PATCH: state.PATCH }),
  }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const e2bSchema = {
  type: "object",
  additionalProperties: false,
  "x-primary-fields": ["template", "timeout_seconds"],
  properties: {
    template: {
      type: "string",
      title: "E2B template name or ID",
      default: "base",
    },
    timeout_seconds: {
      type: "integer",
      title: "Sandbox timeout (seconds)",
      default: 3600,
      minimum: 30,
    },
    max_file_bytes: { type: "integer", title: "File limit", default: 1000 },
    metadata: {
      type: "object",
      title: "Metadata",
      additionalProperties: { type: "string" },
      default: {},
    },
  },
};
const localSchema = {
  type: "object",
  required: ["workspace"],
  additionalProperties: false,
  "x-primary-fields": ["workspace"],
  properties: { workspace: { $ref: "#/$defs/Workspace" } },
  $defs: {
    Workspace: {
      type: "object",
      title: "Workspace",
      required: ["path"],
      properties: { path: { type: "string", title: "Workspace path" } },
    },
  },
};

const dockerSchema = {
  "x-environment-presets": {
    standard: {
      image: "a13n-sandbox:dev",
      execution: "native",
      desktop: false,
    },
    desktop: {
      image: "a13n-sandbox:dev-desktop",
      execution: "envd_http",
      desktop: true,
    },
  },
  type: "object",
  additionalProperties: false,
  "x-primary-fields": [
    "image",
    "environment",
    "init_script",
    "cpus",
    "memory_gb",
    "disable_network",
    "mounts",
  ],
  properties: {
    image: {
      type: "string",
      title: "Image",
      default: "a13n-sandbox:dev",
    },
    execution: {
      type: "string",
      title: "Execution connection",
      default: "native",
      enum: ["native", "envd_http"],
    },
    desktop: { type: "boolean", default: false },
    memory_gb: { type: "number", title: "Memory (GB)" },
    mounts: { type: "array", title: "Mounts", items: { type: "object" } },
    shell: { type: "string", title: "Shell", default: "/bin/sh" },
  },
};

let unmountEditor: (() => void) | undefined;

beforeEach(() => {
  vi.resetAllMocks();
  state.GET.mockImplementation(async (path: string) => ({
    data: {
      items:
        path === "/api/v1/provider-types/{kind}"
          ? [
              {
                type: "e2b",
                display_name: "E2B",
                configuration_schema: { type: "object", properties: {} },
                authentication: { mode: "required" },
                credential_schema: {
                  type: "object",
                  required: ["api_key"],
                  properties: { api_key: { type: "string", title: "API key" } },
                },
                environment_schema: e2bSchema,
                supports_stop: true,
                supports_destroy: true,
              },
              {
                type: "direct_local",
                display_name: "Local directory",
                environment_schema: localSchema,
                supports_stop: false,
                supports_destroy: false,
              },
              {
                type: "docker",
                display_name: "Docker",
                environment_schema: dockerSchema,
                supports_stop: true,
                supports_destroy: true,
              },
            ]
          : [
              { id: "eprov_e2b", type: "e2b", name: "E2B", enabled: true },
              {
                id: "eprov_local",
                type: "direct_local",
                name: "Local",
                enabled: true,
              },
              {
                id: "eprov_docker",
                type: "docker",
                name: "Docker",
                enabled: true,
              },
              {
                id: "eprov_retired",
                type: "retired",
                name: "Retired",
                enabled: true,
              },
            ],
      next_cursor: null,
    },
  }));
  state.POST.mockResolvedValue({ data: { id: "envt_created" } });
  unmountEditor = render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <TemplateConfig close={state.close} />
    </QueryClientProvider>,
  ).unmount;
});

/** Creation opens on the provider catalog; tiles replace the old select. */
async function selectProvider(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  const back = screen.queryByRole("button", {
    name: "Choose a different provider",
  });
  if (back) await user.click(back);
  await user.click(
    await screen.findByRole("button", { name: new RegExp(`^${name}`) }),
  );
}

it("creates an E2B template configuration from ordinary fields without a schema-version input", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "E2B");
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  const template = await screen.findByRole("textbox", {
    name: "E2B template name or ID",
  });
  expect((template as HTMLInputElement).value).toBe("");
  await user.clear(template);
  await user.type(template, "my-template");
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Project template",
  );
  expect(
    screen.queryByRole("textbox", { name: "Template configuration (JSON)" }),
  ).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "Configuration schema version" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Create template" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith("/api/v1/environment-templates", {
      body: {
        name: "Project template",
        description: null,
        provider_id: "eprov_e2b",
        config: {
          recipe: { template: "my-template" },
          stop_after_seconds: null,
          delete_after_seconds: null,
        },
      },
    }),
  );
});

it("offers only providers of an offered type and the idle policy their type supports", async () => {
  const user = userEvent.setup();
  await screen.findByRole("button", { name: /^E2B/ });
  expect(screen.queryByRole("button", { name: /^Retired/ })).toBeNull();
  await selectProvider(user, "Local");
  await user.click(screen.getByRole("button", { name: "Lifecycle" }));
  const stop = screen.getByRole("spinbutton", {
    name: "Stop after idle seconds",
  }) as HTMLInputElement;
  const destroy = screen.getByRole("spinbutton", {
    name: "Delete after idle seconds",
  }) as HTMLInputElement;
  expect(stop.disabled).toBe(true);
  expect(destroy.disabled).toBe(true);
  await selectProvider(user, "E2B");
  await user.click(screen.getByRole("button", { name: "Lifecycle" }));
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Stop after idle seconds",
      }) as HTMLInputElement
    ).disabled,
  ).toBe(false);
});

it("retains per-provider drafts and advanced JSON while switching fields", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "E2B");
  await user.click(screen.getByRole("button", { name: "JSON" }));
  const json = screen.getByRole("textbox", {
    name: "Template configuration (JSON)",
  });
  await user.clear(json);
  await user.paste('{"template":"custom","max_file_bytes":42}');
  await user.click(screen.getByRole("button", { name: "Fields" }));
  await selectProvider(user, "Local");
  expect(
    screen.queryByRole("textbox", { name: "E2B template name or ID" }),
  ).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "Workspace path" }),
    "/work/project",
  );
  await selectProvider(user, "E2B");
  expect(
    (
      screen.getByRole("textbox", {
        name: "E2B template name or ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("custom");
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    JSON.parse(
      (
        screen.getByRole("textbox", {
          name: "Template configuration (JSON)",
        }) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual({ template: "custom", max_file_bytes: 42 });
});

it("rejects invalid advanced configuration without sending a request", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "E2B");
  await user.type(screen.getByRole("textbox", { name: "Name" }), "Invalid");
  await user.click(screen.getByRole("button", { name: "JSON" }));
  await user.clear(
    screen.getByRole("textbox", { name: "Template configuration (JSON)" }),
  );
  await user.paste('{"unsupported":true}');
  await user.click(screen.getByRole("button", { name: "Create template" }));
  await screen.findAllByText(/additional properties/);
  expect(state.POST).not.toHaveBeenCalled();
});

it("shows the Service's refusal of a recipe beside the recipe", async () => {
  const refusal = "config.recipe: invalid for docker: mounts";
  state.POST.mockRejectedValue(
    new ApiError(
      400,
      "invalid_argument",
      refusal,
      {
        field: "config.recipe",
        reason: "invalid for docker: mounts",
      },
      "req_test",
    ),
  );
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  await user.type(screen.getByRole("textbox", { name: "Name" }), "Docker");
  await user.click(screen.getByRole("button", { name: "JSON" }));
  await user.clear(
    screen.getByRole("textbox", { name: "Template configuration (JSON)" }),
  );
  await user.paste('{"mounts":[{"source":"/etc","target":"/host"}]}');
  await user.click(screen.getByRole("button", { name: "Create template" }));
  // The Console holds no Docker policy of its own: the Service decides.
  await waitFor(() => expect(state.POST).toHaveBeenCalledOnce());
  expect(await screen.findAllByText(refusal)).toHaveLength(1);
  expect(screen.queryByText("Something went wrong")).toBeNull();
  expect(state.close).not.toHaveBeenCalled();
});

it("resets advanced JSON drafts and their validation state", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "E2B");
  await user.click(
    screen.getByRole("button", { name: "Advanced environment configuration" }),
  );
  const metadata = screen.getByRole("textbox", { name: "Metadata" });
  await user.clear(metadata);
  await user.paste('{"team":"qa"}');
  await user.clear(metadata);
  await user.paste("[");
  expect((metadata as HTMLTextAreaElement).validity.valid).toBe(false);
  await user.click(screen.getByRole("button", { name: "Reset to defaults" }));
  await user.click(
    screen.getByRole("button", { name: "Advanced environment configuration" }),
  );
  const reset = screen.getByRole("textbox", {
    name: "Metadata",
  }) as HTMLTextAreaElement;
  expect(reset.value).toBe("{}");
  expect(reset.validity.valid).toBe(true);
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Template configuration (JSON)",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("{}");
});

it("saves an existing template's recipe and idle policy against its ETag", async () => {
  unmountEditor?.();
  state.PATCH.mockResolvedValue({ data: { id: "envtpl_test" } });
  const template = {
    id: "envtpl_test",
    organization_id: "org_test",
    workspace_id: "ws_test",
    key: "sandbox",
    name: "Sandbox",
    description: null,
    provider_id: "eprov_e2b",
    config: {
      recipe: { template: "base" },
      stop_after_seconds: 600,
      delete_after_seconds: null,
    },
    enabled: true,
    labels: {},
    version: 4,
    created_by_id: "usr_test",
    updated_by_id: "usr_test",
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  };
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <TemplateConfig
        template={{ value: template, etag: '"envtpl_test:4"' }}
        close={state.close}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Official default" });
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(
      "/api/v1/environment-templates/{template_id}",
      {
        params: {
          path: { template_id: "envtpl_test" },
        },
        headers: { "If-Match": '"envtpl_test:4"' },
        body: {
          provider_id: "eprov_e2b",
          config: {
            recipe: { template: "base" },
            stop_after_seconds: 600,
            delete_after_seconds: null,
          },
        },
      },
    ),
  );
  await waitFor(() => expect(state.close).toHaveBeenCalledOnce());
});

function mountCreationPage() {
  unmountEditor?.();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <TemplateConfig standalone close={state.close} />
    </QueryClientProvider>,
  );
}

it("keeps runtime drafts separate and saves using the matching connection", async () => {
  mountCreationPage();
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "My workspace",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Description" }),
    "Reusable setup",
  );
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  expect(
    screen
      .getByRole("tab", { name: "Cloud hosting" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.queryByRole("button", { name: /Local directory/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  const template = screen.getByRole("textbox", {
    name: "E2B template name or ID",
  });
  await user.clear(template);
  await user.type(template, "custom");
  await user.click(screen.getByRole("tab", { name: "Own hosting" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "E2B" })).toBeNull(),
  );
  expect(
    screen.queryByRole("textbox", { name: "E2B template name or ID" }),
  ).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Create template",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: /Local directory/ }));
  await user.type(
    screen.getByRole("textbox", { name: "Workspace path" }),
    "/projects",
  );
  await user.click(screen.getByRole("tab", { name: "Cloud hosting" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "E2B template name or ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("custom");
  await user.click(screen.getByRole("combobox", { name: "Connection" }));
  expect(await screen.findByRole("option", { name: "E2B" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Local" })).toBeNull();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("tab", { name: "Own hosting" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Workspace path",
      }) as HTMLInputElement
    ).value,
  ).toBe("/projects");
  await user.click(screen.getByRole("button", { name: "Create template" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith("/api/v1/environment-templates", {
      body: {
        name: "My workspace",
        description: "Reusable setup",
        provider_id: "eprov_local",
        config: {
          recipe: { workspace: { path: "/projects" } },
          stop_after_seconds: null,
          delete_after_seconds: null,
        },
      },
    }),
  );
});

it("creates a connection in place, clears canceled credentials and retains the recipe through errors", async () => {
  let saved = false;
  const get = state.GET.getMockImplementation()!;
  state.GET.mockImplementation(async (path: string) => {
    const result = await get(path);
    if (path !== "/api/v1/provider-types/{kind}")
      result.data.items = saved ? [connection] : [];
    return result;
  });
  const connection = {
    id: "eprov_new",
    type: "e2b",
    name: "E2B",
    enabled: true,
  };
  state.POST.mockRejectedValueOnce(new Error("Connection rejected"))
    .mockImplementationOnce(async () => {
      saved = true;
      return { data: connection };
    })
    .mockResolvedValueOnce({ data: { id: "envt_created" } });
  mountCreationPage();
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Cloud template",
  );
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  const recipe = screen.getByRole("textbox", {
    name: "E2B template name or ID",
  });
  await user.clear(recipe);
  await user.type(recipe, "custom-base");
  // No connections exist: changing runtime must still isolate its draft.
  await user.click(screen.getByRole("tab", { name: "Own hosting" }));
  await user.click(screen.getByRole("button", { name: /Local directory/ }));
  await user.type(
    screen.getByRole("textbox", { name: "Workspace path" }),
    "/local-only",
  );
  await user.click(screen.getByRole("tab", { name: "Cloud hosting" }));
  await user.click(screen.getByRole("button", { name: "E2B" }));
  expect(
    (
      screen.getByRole("button", {
        name: "Create template",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Manage connections" }));
  await user.click(
    await screen.findByRole("button", { name: "Add connection" }),
  );
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  let dialog = within(screen.getByRole("dialog"));
  await user.type(dialog.getByLabelText("API key"), "discarded-test-key");
  await user.click(dialog.getByRole("button", { name: "Cancel" }));
  await screen.findByRole("dialog", { name: "Manage connections" });
  await user.click(screen.getByRole("button", { name: "Add connection" }));
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  dialog = within(screen.getByRole("dialog"));
  expect((dialog.getByLabelText("API key") as HTMLInputElement).value).toBe("");
  await user.type(dialog.getByLabelText("API key"), "test-key");
  await user.click(dialog.getByRole("button", { name: "Add connection" }));
  await screen.findByText("Connection rejected");
  expect(state.POST).toHaveBeenCalledTimes(1);
  expect(state.close).not.toHaveBeenCalled();
  await user.click(dialog.getByRole("button", { name: "Add connection" }));
  const management = await screen.findByRole("dialog", {
    name: "Manage connections",
  });
  await user.click(within(management).getByRole("button", { name: "Close" }));
  await user.click(screen.getByRole("combobox", { name: "Connection" }));
  await user.click(await screen.findByRole("option", { name: "E2B" }));
  expect(state.POST).toHaveBeenCalledTimes(2);
  expect(state.POST).toHaveBeenLastCalledWith("/api/v1/environment-providers", {
    body: {
      name: "E2B",
      type: "e2b",
      config: {},
      credential: { api_key: "test-key" },
    },
  });
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("Cloud template");
  expect(
    (
      screen.getByRole("textbox", {
        name: "E2B template name or ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("custom-base");
  await user.click(screen.getByRole("button", { name: "Create template" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenLastCalledWith(
      "/api/v1/environment-templates",
      {
        body: {
          name: "Cloud template",
          description: null,
          provider_id: connection.id,
          config: {
            recipe: { template: "custom-base" },
            stop_after_seconds: null,
            delete_after_seconds: null,
          },
        },
      },
    ),
  );
  expect(state.POST).toHaveBeenCalledTimes(3);
});

it("keeps a late connection result without replacing a newly selected runtime", async () => {
  const created = {
    id: "eprov_late",
    type: "e2b",
    name: "Another E2B",
    enabled: true,
  };
  let resolve!: (value: { data: typeof created }) => void;
  const pending = new Promise<{ data: typeof created }>((done) => {
    resolve = done;
  });
  let saved = false;
  const get = state.GET.getMockImplementation()!;
  state.GET.mockImplementation(async (path: string) => {
    const result = await get(path);
    if (saved && path !== "/api/v1/provider-types/{kind}")
      result.data.items = [...result.data.items, created];
    return result;
  });
  state.POST.mockReturnValueOnce(pending);
  mountCreationPage();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  await user.click(screen.getByRole("button", { name: "Manage connections" }));
  await user.click(
    await screen.findByRole("button", { name: "Add connection" }),
  );
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  const dialog = within(screen.getByRole("dialog"));
  await user.type(dialog.getByLabelText("API key"), "test-key");
  await user.click(dialog.getByRole("button", { name: "Add connection" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledOnce());
  await user.click(dialog.getByRole("button", { name: "Close" }));
  const management = await screen.findByRole("dialog", {
    name: "Manage connections",
  });
  await user.click(within(management).getByRole("button", { name: "Close" }));
  await user.click(screen.getByRole("tab", { name: "Own hosting" }));
  await user.click(screen.getByRole("button", { name: /Local directory/ }));
  await act(async () => {
    saved = true;
    resolve({ data: created });
    await pending;
  });
  expect(
    screen.getByRole("combobox", { name: "Connection" }).textContent,
  ).toContain("Local");
  await user.click(screen.getByRole("tab", { name: "Cloud hosting" }));
  await user.click(screen.getByRole("button", { name: "E2B" }));
  expect(
    screen.getByRole("combobox", { name: "Connection" }).textContent,
  ).not.toContain("Another E2B");
  await user.click(screen.getByRole("combobox", { name: "Connection" }));
  expect(
    await screen.findByRole("option", { name: "Another E2B" }),
  ).toBeTruthy();
  expect(state.POST).toHaveBeenCalledOnce();
});

it("preserves advanced Docker settings when switching desktop capability and JSON mode", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  await user.click(screen.getByRole("button", { name: "JSON" }));
  const json = screen.getByRole("textbox", {
    name: "Template configuration (JSON)",
  });
  await user.clear(json);
  await user.paste(
    '{"image":"sandbox:desktop","shell":"/bin/bash","desktop":false}',
  );
  await user.click(screen.getByRole("button", { name: "Fields" }));
  await user.click(screen.getByRole("switch", { name: "Desktop capability" }));
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    JSON.parse(
      (
        screen.getByRole("textbox", {
          name: "Template configuration (JSON)",
        }) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual({
    image: "sandbox:desktop",
    shell: "/bin/bash",
    desktop: true,
    execution: "envd_http",
  });
  await user.click(screen.getByRole("button", { name: "Fields" }));
  await user.click(
    screen.getByRole("button", { name: "Standard environment" }),
  );
  expect(screen.queryByRole("textbox", { name: "Container image" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Custom image" }));
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    JSON.parse(
      (
        screen.getByRole("textbox", {
          name: "Template configuration (JSON)",
        }) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual({
    image: "sandbox:desktop",
    shell: "/bin/bash",
    desktop: true,
    execution: "envd_http",
  });
});

it("creates a desktop preset without exposing image or execution controls", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Linux desktop",
  );
  expect(
    screen
      .getByRole("button", { name: "Standard environment" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: "Desktop environment" }));
  expect(screen.queryByRole("textbox", { name: "Container image" })).toBeNull();
  expect(
    screen.queryByRole("combobox", { name: "Execution connection" }),
  ).toBeNull();
  expect(
    screen.queryByRole("switch", { name: "Desktop capability" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "JSON" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Create template" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledOnce());
  expect(state.POST.mock.calls[0]?.[1].body.config.recipe).toEqual({
    image: "a13n-sandbox:dev-desktop",
    execution: "envd_http",
    desktop: true,
  });
});

it("manages connections in place without losing the template draft", async () => {
  mountCreationPage();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "E2B" }));
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Unsaved template",
  );
  await user.click(screen.getByRole("button", { name: "Custom template" }));
  const recipe = screen.getByRole("textbox", {
    name: "E2B template name or ID",
  });
  await user.clear(recipe);
  await user.type(recipe, "custom-recipe");
  expect(screen.queryByRole("button", { name: "Add connection" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Manage connections" }));
  const management = await screen.findByRole("dialog", {
    name: "Manage connections",
  });
  expect(
    await within(management).findByRole("table", { name: "Connections" }),
  ).toBeTruthy();
  await user.click(within(management).getByRole("button", { name: "Close" }));
  expect(
    screen.getByRole<HTMLInputElement>("textbox", { name: "Name" }).value,
  ).toBe("Unsaved template");
  expect(
    screen.getByRole<HTMLInputElement>("textbox", {
      name: "E2B template name or ID",
    }).value,
  ).toBe("custom-recipe");
  expect(state.POST).not.toHaveBeenCalled();
});
