import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ImportAgentForm } from "./import";
import { initialConfig } from "./configuration";
import { agentFile, serializeAgentFile } from "./transfer";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_target" },
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const config = {
  ...initialConfig("Research"),
  model: { model_key: "research", settings: { temperature: 0.4 } },
  plugins: [{ instance_name: "memory", plugin_key: "memory", config: {} }],
  secret_requirements: [{ key: "token", required: true }],
};
const source = serializeAgentFile(
  agentFile({ name: "Research", description: "Keep me" }, config),
);

function setup() {
  http.GET.mockResolvedValue({
    data: {
      items: [
        {
          id: "mdl_local",
          key: "research",
          name: "Research model",
          enabled: true,
        },
      ],
    },
  });
  const onSuccess = vi.fn();
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const rendered = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ImportAgentForm onCancel={vi.fn()} onSuccess={onSuccess} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, user: userEvent.setup(), onSuccess };
}

it("previews before creation and retries the same request without losing advanced fields", async () => {
  const { user, onSuccess } = setup();
  http.POST.mockRejectedValueOnce(
    new Error("Network interrupted"),
  ).mockResolvedValueOnce({ data: { agent: { key: "imported" } } });
  await user.click(screen.getByLabelText("Agent YAML"));
  await user.paste(source);
  await user.click(screen.getByRole("button", { name: "Review import" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Create agent",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await screen.findByText("Network interrupted");
  expect(http.POST.mock.calls[0]?.[1].body).toEqual({
    name: "Research",
    description: "Keep me",
    config,
  });
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
  expect(http.POST.mock.calls[0]?.[1].params.header["Idempotency-Key"]).toBe(
    http.POST.mock.calls[1]?.[1].params.header["Idempotency-Key"],
  );
});

it("blocks missing dependencies and requires an explicit replacement", async () => {
  const { user } = setup();
  await user.click(screen.getByLabelText("Agent YAML"));
  await user.paste(source.replace("model_key: research", "model_key: missing"));
  await user.click(screen.getByRole("button", { name: "Review import" }));
  await screen.findByText(
    "Dependency unavailable. Choose a resource in this workspace.",
  );
  expect(
    (screen.getByRole("button", { name: "Create agent" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("combobox", { name: "model.model_key" }));
  await user.keyboard("{ArrowDown}");
  await user.click(
    await screen.findByRole("option", { name: "Research model · research" }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Create agent",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Edit YAML" }));
  expect(
    (screen.getByLabelText("Agent YAML") as HTMLTextAreaElement).value,
  ).toContain("model_key: research");
  expect(http.POST).not.toHaveBeenCalled();
});

it("blocks an unavailable root Environment template until mapped in the destination", async () => {
  const { user } = setup();
  http.GET.mockImplementation(async (url: string) => ({
    data: url.endsWith("/environment-templates")
      ? {
          items: [
            {
              id: "et_fedcba9876543210",
              name: "Local sandbox",
              version: 2,
              current_revision_id: "etr_fedcba9876543210",
            },
          ],
        }
      : {
          items: [
            {
              id: "mdl_local",
              key: "research",
              name: "Research model",
              enabled: true,
            },
          ],
        },
  }));
  const sourceWithTemplate = serializeAgentFile(
    agentFile(
      { name: "Research", description: null },
      {
        ...config,
        default_environment_template_id: "et_0123456789abcdef",
      },
    ),
  );
  await user.click(screen.getByLabelText("Agent YAML"));
  await user.paste(sourceWithTemplate);
  await user.click(screen.getByRole("button", { name: "Review import" }));
  await screen.findByText(
    "Dependency unavailable. Choose a resource in this workspace.",
  );
  expect(
    (screen.getByRole("button", { name: "Create agent" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(
    screen.getByRole("combobox", { name: "default_environment_template_id" }),
  );
  await user.keyboard("{ArrowDown}");
  await user.click(
    await screen.findByRole("option", { name: "Local sandbox · v2" }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Create agent",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Edit YAML" }));
  expect(
    (screen.getByLabelText("Agent YAML") as HTMLTextAreaElement).value,
  ).toContain("default_environment_template_id: et_fedcba9876543210");
  expect(http.POST).not.toHaveBeenCalled();
});

it("uploads a file, validates its contents, and rejects unsupported versions without a write", async () => {
  const { user, container } = setup();
  const file = new File(["schema_version: 9"], "agent.yaml", {
    type: "application/yaml",
  });
  Object.defineProperty(file, "text", {
    value: async () => "schema_version: 9",
  });
  await user.upload(
    container.querySelector<HTMLInputElement>('input[type="file"]')!,
    file,
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Agent YAML") as HTMLTextAreaElement).value,
    ).toBe("schema_version: 9"),
  );
  await user.click(screen.getByRole("button", { name: "Review import" }));
  await screen.findByText(
    "Unsupported Agent file version. Expected schema_version: 1.",
  );
  expect(http.GET).not.toHaveBeenCalled();
  expect(http.POST).not.toHaveBeenCalled();
});
