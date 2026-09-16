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

beforeEach(() => {
  vi.resetAllMocks();
  state.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.endsWith("environment-provider-types")
        ? [
            {
              type: "a13n.e2b",
              supports_managed: true,
              supports_stop: true,
              supports_destroy: true,
              configuration_versions: ["1"],
              template_configuration_schemas: { "1": e2bSchema },
            },
            {
              type: "a13n.docker",
              supports_managed: true,
              supports_stop: false,
              supports_destroy: false,
              configuration_versions: ["1"],
              template_configuration_schemas: { "1": localSchema },
            },
            { type: "a13n.http-envd", supports_managed: false },
          ]
        : [
            { id: "envp_e2b", type: "a13n.e2b", name: "E2B", enabled: true },
            {
              id: "envp_local",
              type: "a13n.docker",
              name: "Local",
              enabled: true,
            },
            {
              id: "envp_http",
              type: "a13n.http-envd",
              name: "External",
              enabled: true,
            },
          ],
      next_cursor: null,
    },
  }));
  state.POST.mockResolvedValue({ data: { id: "envt_created" } });
  render(
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
  );
});

async function selectProvider(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await user.click(screen.getByRole("combobox", { name: "Provider" }));
  await user.click(await screen.findByRole("option", { name }));
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
          configuration_schema_version: "1",
          configuration: { template: "my-template" },
        }),
      }),
    ),
  );
});

it("retains per-provider drafts and advanced JSON while switching fields", async () => {
  const user = userEvent.setup();
  await selectProvider(user, "E2B");
  await user.click(screen.getByRole("tab", { name: "JSON" }));
  const json = screen.getByRole("textbox", {
    name: "Template configuration (JSON)",
  });
  await user.clear(json);
  await user.paste('{"template":"custom","max_file_bytes":42}');
  await user.click(screen.getByRole("tab", { name: "Fields" }));
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
  await user.click(screen.getByRole("tab", { name: "JSON" }));
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
  await user.click(screen.getByRole("tab", { name: "JSON" }));
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
  await user.click(screen.getByRole("tab", { name: "JSON" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Template configuration (JSON)",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("{}");
});
