import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { TemplateConfig } from "./template-config";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  close: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: state.GET, POST: state.POST } }),
}));
vi.mock("../../layout/workspace", () => ({
  useAccess: () => ({ workspace: { id: "ws_test" } }),
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
      items: path.endsWith("environment-provider-types")
        ? [
            {
              type: "e2b",
              supports_managed: true,
              supports_stop: true,
              supports_destroy: true,
              template_configuration_schema: e2bSchema,
            },
            {
              type: "direct_local",
              supports_managed: true,
              supports_stop: false,
              supports_destroy: false,
              template_configuration_schema: localSchema,
            },
            {
              type: "docker",
              supports_managed: true,
              supports_stop: true,
              supports_destroy: true,
              template_configuration_schema: dockerSchema,
            },
            { type: "http_envd", supports_managed: false },
          ]
        : [
            { id: "envp_e2b", type: "e2b", name: "E2B", enabled: true },
            {
              id: "envp_local",
              type: "direct_local",
              name: "Local",
              enabled: true,
            },
            {
              id: "envp_docker",
              type: "docker",
              name: "Docker",
              enabled: true,
            },
            {
              id: "envp_http",
              type: "http_envd",
              name: "External",
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
      <TemplateConfig
        scope={{ kind: "workspace", id: "ws_test" }}
        close={state.close}
      />
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
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/environment-templates",
      expect.objectContaining({
        body: expect.objectContaining({
          name: "Project template",
          provider_id: "envp_e2b",
          configuration: { template: "my-template" },
        }),
      }),
    ),
  );
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

it("tests the selected Docker image on the Worker without saving the template", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  const image = screen.getByRole("textbox", { name: "Image" });
  await user.clear(image);
  await user.type(image, "my-env:dev");
  expect(screen.getByRole("button", { name: "Mounts" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Test image" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/environment-providers/{provider_id}/test-image",
      expect.objectContaining({
        params: { path: { provider_id: "envp_docker" } },
        body: expect.objectContaining({
          workspace_id: "ws_test",
          configuration: { image: "my-env:dev" },
          request_id: expect.stringMatching(/^envtest_[0-9a-f]{32}$/),
        }),
      }),
    ),
  );
  expect(state.close).not.toHaveBeenCalled();
});

it("aborts an image test when its draft changes and ignores a late result", async () => {
  let finish: ((value: unknown) => void) | undefined;
  state.POST.mockImplementation((path: string) =>
    path.endsWith("/cancel")
      ? Promise.resolve({ data: null })
      : new Promise((resolve) => {
          finish = resolve;
        }),
  );
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  await user.click(screen.getByRole("button", { name: "Test image" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalled());
  const signal = state.POST.mock.calls[0][1].signal as AbortSignal;
  expect(signal.aborted).toBe(false);
  await user.type(screen.getByRole("textbox", { name: "Image" }), "changed");
  expect(signal.aborted).toBe(true);
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/environment-providers/{provider_id}/test-image/{request_id}/cancel",
      expect.objectContaining({
        params: {
          path: {
            provider_id: "envp_docker",
            request_id: expect.stringMatching(/^envtest_[0-9a-f]{32}$/),
          },
        },
      }),
    ),
  );
  finish?.({ data: { image_id: "sha256:old", checks: ["files"] } });
  await waitFor(() => expect(screen.queryByText(/sha256:old/)).toBeNull());
});

it("cancels the Worker request when the editor closes", async () => {
  state.POST.mockImplementation((path: string) =>
    path.endsWith("/cancel")
      ? Promise.resolve({ data: null })
      : new Promise(() => undefined),
  );
  const user = userEvent.setup();
  await selectProvider(user, "Docker");
  await user.click(screen.getByRole("button", { name: "Test image" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalled());
  const signal = state.POST.mock.calls[0][1].signal as AbortSignal;
  unmountEditor?.();
  expect(signal.aborted).toBe(true);
  expect(state.POST).toHaveBeenCalledWith(
    "/api/v1/environment-providers/{provider_id}/test-image/{request_id}/cancel",
    expect.objectContaining({
      params: {
        path: {
          provider_id: "envp_docker",
          request_id: expect.stringMatching(/^envtest_[0-9a-f]{32}$/),
        },
      },
    }),
  );
});
