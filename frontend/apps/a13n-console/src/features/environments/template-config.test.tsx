import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
      default: "a13n-docker-environment:dev",
    },
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
                environment_schema: e2bSchema,
                supports_stop: true,
                supports_destroy: true,
              },
              {
                type: "direct_local",
                environment_schema: localSchema,
                supports_stop: false,
                supports_destroy: false,
              },
              {
                type: "docker",
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
  const template = await screen.findByRole("textbox", {
    name: "E2B template name or ID",
  });
  expect((template as HTMLInputElement).value).toBe("base");
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
  await screen.findByRole("textbox", { name: "E2B template name or ID" });
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
