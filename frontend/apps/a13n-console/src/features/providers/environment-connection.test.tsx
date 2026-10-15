import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { EnvironmentConnectionDelete } from "./environment-connection-delete";
import { ProviderForm } from "./kind-providers";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  DELETE: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state, workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const provider: Schema["Provider"] = {
  id: "eprov_test",
  name: "Docker connection",
  type: "docker",
  config: { docker_host: "tcp://old:2375" },
  enabled: true,
  credential_configured: false,
  version: 2,
  organization_id: "org_test",
  workspace_id: "ws_test",
  created_at: "2026-10-14T00:00:00Z",
  updated_at: "2026-10-14T00:00:00Z",
  created_by_id: null,
  updated_by_id: null,
  header_names: [],
};
const definition: Schema["ProviderType"] = {
  type: "docker",
  display_name: "Docker",
  authentication: { mode: "forbidden" },
  configuration_schema: {
    type: "object",
    properties: { docker_host: { type: "string", title: "Docker host" } },
  },
  credential_schema: null,
  setup_label: null,
  setup_url: null,
  supports_test: false,
};
function mount(component: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(<QueryClientProvider client={cache}>{component}</QueryClientProvider>);
  return cache;
}
const emptyRefs = {
  template_count: 0,
  templates: [],
  environment_count: 0,
  environments: [],
};
it("blocks deletion for disabled templates and historical environments", async () => {
  state.GET.mockResolvedValue({
    data: {
      template_count: 1,
      templates: [{ id: "tpl_old", name: "Old template", status: "disabled" }],
      environment_count: 1,
      environments: [{ id: "env_old", name: "Old desktop", status: "deleted" }],
    },
  });
  const user = userEvent.setup();
  mount(
    <EnvironmentConnectionDelete
      provider={provider}
      etag='"eprov_test:2"'
      onDeleted={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Delete connection" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText("Old template");
  expect(within(dialog).getByText("Old desktop")).toBeTruthy();
  const button = within(dialog).getByRole<HTMLButtonElement>("button", {
    name: "Delete connection",
  });
  expect(button.disabled).toBe(true);
  await user.click(button);
  expect(state.DELETE).not.toHaveBeenCalled();
});
it("deletes an unused connection with its ETag and refreshes the picker", async () => {
  state.GET.mockResolvedValue({ data: emptyRefs });
  state.DELETE.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  const user = userEvent.setup(),
    deleted = vi.fn();
  const cache = mount(
    <EnvironmentConnectionDelete
      provider={provider}
      etag='"eprov_test:2"'
      onDeleted={deleted}
    />,
  );
  const invalidate = vi.spyOn(cache, "invalidateQueries");
  await user.click(screen.getByRole("button", { name: "Delete connection" }));
  const dialog = await screen.findByRole("dialog");
  const button = within(dialog).getByRole<HTMLButtonElement>("button", {
    name: "Delete connection",
  });
  await waitFor(() => expect(button.disabled).toBe(false));
  await user.click(button);
  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  expect(state.DELETE).toHaveBeenCalledWith(
    "/api/v1/environment-providers/{provider_id}",
    expect.objectContaining({ headers: { "If-Match": '"eprov_test:2"' } }),
  );
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ["environment-provider-options"],
  });
});
it("refreshes references when a new reference races with confirmation", async () => {
  state.GET.mockResolvedValueOnce({ data: emptyRefs }).mockResolvedValue({
    data: {
      ...emptyRefs,
      template_count: 1,
      templates: [{ id: "tpl_new", name: "New reference", status: "enabled" }],
    },
  });
  state.DELETE.mockRejectedValue(new Error("Connection is now in use"));
  const user = userEvent.setup(),
    deleted = vi.fn();
  mount(
    <EnvironmentConnectionDelete
      provider={provider}
      etag='"eprov_test:2"'
      onDeleted={deleted}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Delete connection" }));
  const dialog = await screen.findByRole("dialog");
  const button = within(dialog).getByRole<HTMLButtonElement>("button", {
    name: "Delete connection",
  });
  await waitFor(() => expect(button.disabled).toBe(false));
  await user.click(button);
  await within(dialog).findByText("New reference");
  expect(button.disabled).toBe(true);
  expect(deleted).not.toHaveBeenCalled();
});
it("edits connection settings without submitting the enclosing template", async () => {
  const user = userEvent.setup(),
    close = vi.fn(),
    outerSubmit = vi.fn();
  state.PATCH.mockResolvedValue({ data: { ...provider, version: 3 } });
  mount(
    <div onSubmit={outerSubmit}>
      <ProviderForm
        kind="environment"
        initial={{ value: provider, etag: '"eprov_test:2"' }}
        definitions={[definition]}
        close={close}
        reload={async () => {}}
      />
    </div>,
  );
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  const input = screen.getByLabelText("Docker host");
  await user.clear(input);
  await user.type(input, "tcp://new:2375");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(state.PATCH).toHaveBeenCalledWith(
    "/api/v1/environment-providers/{provider_id}",
    expect.objectContaining({
      body: {
        name: provider.name,
        enabled: true,
        config: { docker_host: "tcp://new:2375" },
      },
    }),
  );
  expect(outerSubmit).not.toHaveBeenCalled();
});
it("preserves the saved credential when editing an authenticated connection", async () => {
  const user = userEvent.setup(),
    close = vi.fn();
  const saved = {
    ...provider,
    name: "E2B connection",
    type: "e2b",
    config: { api_url: "https://old.example" },
    credential_configured: true,
  };
  const auth = {
    ...definition,
    type: "e2b",
    display_name: "E2B",
    configuration_schema: {
      type: "object",
      properties: { api_url: { type: "string", title: "API URL" } },
    },
    authentication: { mode: "required" as const },
    credential_schema: {
      type: "object",
      required: ["api_key"],
      properties: { api_key: { type: "string", title: "API key" } },
    },
  };
  state.PATCH.mockResolvedValue({ data: { ...saved, version: 3 } });
  mount(
    <ProviderForm
      kind="environment"
      initial={{ value: saved, etag: '"eprov_test:2"' }}
      definitions={[auth]}
      close={close}
      reload={async () => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  const input = screen.getByLabelText("API URL");
  await user.clear(input);
  await user.type(input, "https://new.example");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledOnce());
  expect(state.PATCH).toHaveBeenCalledWith(
    "/api/v1/environment-providers/{provider_id}",
    expect.objectContaining({
      headers: { "If-Match": '"eprov_test:2"' },
      body: {
        name: saved.name,
        enabled: true,
        config: { api_url: "https://new.example" },
      },
    }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
});
